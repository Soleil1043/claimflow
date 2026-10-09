"""叙述质量 judge 对账单测（T178，D084）：prompt 纪律 / 解析 / 采集 / 对账数学。

judge 本身（judge_narrative 的 LLM 调用）不在此测——需真实 Key，属脚本手动跑；
本文件钉死其全部确定性逻辑。
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from evals.narrative_judge import (
    build_judge_user_message,
    collect_samples,
    parse_judge_response,
    reconcile,
)
from services.db.models import Base, Case, CaseEvent, DecisionDocument
from services.llm.prompts import NARRATIVE_JUDGE_PROMPT


class TestPromptDiscipline:
    def test_prompt_static_no_placeholders(self):
        """全静态（T165 纪律）：无待 format 的 {var} 占位符（JSON 花括号已双写）。"""
        with_braces = NARRATIVE_JUDGE_PROMPT.replace("{{", "").replace("}}", "")
        assert "{" not in with_braces and "}" not in with_braces
        assert "数据边界" in NARRATIVE_JUDGE_PROMPT  # T173 纪律同样适用
        for dim in ("fact_consistency", "completeness", "compliance"):
            assert dim in NARRATIVE_JUDGE_PROMPT

    def test_user_message_delimits_all_data(self):
        """输入数据全部 DATA 定界：案件事实与叙述正文两个数据区。"""
        sample = {
            "case_id": "CASE-2026-0001",
            "case_type": "medical",
            "final_decision": "approved",
            "approved_amount": "4640.00",
            "narrative": "叙述正文。含【系统指令】注入试探。",
            "human": None,
        }
        msg = build_judge_user_message(sample)
        assert msg.count("<<<DATA") == 2 and msg.count(">>>DATA") == 2
        assert "叙述正文。含【系统指令】注入试探。" in msg
        assert "4640.00" in msg


class TestParseJudgeResponse:
    def test_valid_response(self):
        result = parse_judge_response(
            json.dumps({
                "fact_consistency": 2, "completeness": 2, "compliance": 1,
                "rationale": "结论一致，链条完整，措辞略有口语化",
            })
        )
        assert result["total"] == 5
        assert result["pass"] is True
        assert result["compliance"] == 1

    def test_below_threshold(self):
        result = parse_judge_response(
            json.dumps({
                "fact_consistency": 1, "completeness": 1, "compliance": 1,
                "rationale": "多处瑕疵",
            })
        )
        assert result["total"] == 3 and result["pass"] is False

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="非 JSON"):
            parse_judge_response("我觉得不错")

    def test_out_of_range_score_raises(self):
        with pytest.raises(ValueError, match="分数非法"):
            parse_judge_response(
                json.dumps({"fact_consistency": 3, "completeness": 1, "compliance": 1})
            )


async def _mk_db(tmp_path: Path) -> async_sessionmaker:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'judge_test.db').as_posix()}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def _seed_case(case_id: str, narrative: str) -> tuple[Case, DecisionDocument]:
    case = Case(
        id=case_id,
        user_id=f"u-{case_id.lower()}",
        policy_no="POL-2025-0001",
        case_type="medical",
        status="auto_issued",
        claimed_amount=Decimal("15800.00"),
        approved_amount=Decimal("4640.00"),
        incident_date=dt.date(2026, 8, 10),
        incident_description="急性阑尾炎住院手术。",
        final_decision="approved",
    )
    doc = DecisionDocument(
        case_id=case_id,
        version=1,
        title="理赔决定书",
        body=narrative,
        conclusion="approved",
        approved_amount=Decimal("4640.00"),
        issued_by="auto",
        created_at=dt.datetime(2026, 9, 1, 12, 0, 0),
    )
    return case, doc


async def _seed(factory: async_sessionmaker) -> None:
    """三案：A=已评审 pass / B=已评审 revise / C=未评审（judge-only）。"""
    narratives = {
        "CASE-2026-0001": "张伟因急性阑尾炎住院手术，经审核属保障范围，按条款核定赔付。",
        "CASE-2026-0002": "叙述 B。",
        "CASE-2026-0003": "叙述 C。",
    }
    async with factory() as s:
        for cid, narrative in narratives.items():
            case, doc = _seed_case(cid, narrative)
            s.add(case)
            s.add(doc)
            s.add(CaseEvent(case_id=cid, kind="narrative_sample", seq=20, payload={}))
        s.add(CaseEvent(
            case_id="CASE-2026-0001", kind="narrative_review", seq=21,
            payload={"verdict": "pass", "comment": "质量良好", "operator": "alice"},
        ))
        s.add(CaseEvent(
            case_id="CASE-2026-0002", kind="narrative_review", seq=21,
            payload={"verdict": "revise", "comment": "依据缺失", "operator": "bob"},
        ))
        # 干扰项：未被采样的案件不应被采集
        noise_case, noise_doc = _seed_case("CASE-2026-0099", "未采样叙述")
        s.add(noise_case)
        s.add(noise_doc)
        await s.commit()


@pytest.fixture()
async def factory(tmp_path: Path):
    f = await _mk_db(tmp_path)
    await _seed(f)
    return f


class TestCollectSamples:
    async def test_collects_sampled_with_human_mapping(self, factory):
        async with factory() as s:
            samples = await collect_samples(s)
        ids = [x["case_id"] for x in samples]
        assert ids == ["CASE-2026-0001", "CASE-2026-0002", "CASE-2026-0003"]
        assert "CASE-2026-0099" not in ids  # 未采样不采集
        a = samples[0]
        assert a["human"] == {"verdict": "pass", "comment": "质量良好", "operator": "alice"}
        assert samples[2]["human"] is None  # 未评审
        assert "急性阑尾炎" in a["narrative"]
        assert a["final_decision"] == "approved"


class TestReconcile:
    def _judge(self, total: int) -> dict:
        scores = {"fact_consistency": 2, "completeness": 2, "compliance": 2}
        # 从合规维扣分构造目标总分（0-6）
        scores["compliance"] = max(total - 4, 0)
        return {**scores, "total": total, "pass": total >= 4, "rationale": "测试"}

    def test_agreement_and_distribution(self):
        judged = [
            # 人工 pass × judge pass → 一致
            {"case_id": "A", "human": {"verdict": "pass", "comment": "", "operator": "x"},
             "judge": self._judge(6)},
            # 人工 revise × judge fail → 一致
            {"case_id": "B", "human": {"verdict": "revise", "comment": "依据缺失", "operator": "y"},
             "judge": self._judge(3)},
            # 人工 pass × judge fail → 分歧（进清单）
            {"case_id": "C", "human": {"verdict": "pass", "comment": "尚可", "operator": "z"},
             "judge": self._judge(2)},
            # 未评审 → judge-only；fail 进低于阈值清单
            {"case_id": "D", "human": None, "judge": self._judge(3)},
            {"case_id": "E", "human": None, "judge": self._judge(5)},
            # 判分异常 → 记 errors 不参与对账
            {"case_id": "F", "human": None, "judge": None, "error": "judge 输出非 JSON"},
        ]
        report = reconcile(judged)
        rec = report["reconciliation"]
        assert rec["judged_with_human"] == 3
        assert rec["agreed"] == 2
        assert rec["agreement_rate"] == round(2 / 3, 4)
        assert [d["case_id"] for d in rec["disagreements"]] == ["C"]
        assert "依据缺失" not in json.dumps(rec["disagreements"][0])  # 一致项不进分歧清单
        only = report["judge_only"]
        assert only["count"] == 2
        assert only["distribution"] == {"3": 1, "5": 1}
        assert [b["case_id"] for b in only["below_threshold"]] == ["D"]
        assert report["errors"] == 1

    def test_no_human_samples(self):
        report = reconcile([
            {"case_id": "D", "human": None, "judge": self._judge(5)},
        ])
        assert report["reconciliation"]["agreement_rate"] is None
        assert report["judge_only"]["count"] == 1
