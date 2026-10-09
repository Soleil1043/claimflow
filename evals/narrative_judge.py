"""叙述质量 LLM judge × 人工抽评对账（T178，D084：观测层，不进门禁）。

定位：给 T139 的 5% 叙述抽评加观测性 judge——
- 已有人工 verdict 的样本 → judge×人工**一致率**（一致率本身是指标：judge 的
  质量用人工金标准度量，而不是被信任）
- 未评审样本 → judge-only 分布 + 低于阈值清单（judge 扩大抽评覆盖的实际用途）
- 温度 0 单次判分 + 方差明示（RAGAS 同款备注）；调用经 phase_ainvoke(
  stage="narrative_judge") 自动进 T164/T177 观测与币值口径

数据源（与 interventions 叙述抽评端点同源）：narrative_sample 事件（采样）+
narrative_review 事件（人工 verdict）+ 最新版决定书（叙述在 body）。
CLI 见 scripts/judge_narrative.py；本模块只放可单测的纯逻辑与查询。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.db.models import Case, CaseEvent, DecisionDocument

# rubric 三维（v1 T068 口径延续）：各 0-2 分，总分 ≥4 = pass
JUDGE_DIMENSIONS = ("fact_consistency", "completeness", "compliance")
JUDGE_PASS_TOTAL = 4


async def collect_samples(session: AsyncSession) -> list[dict[str, Any]]:
    """采集叙述抽评样本：采样案件 + 案件结论事实 + 最新版叙述 + 人工 verdict。

    - 采样口径与 /interventions/narrative-samples 同源（narrative_sample 事件，
      同案多采以最新为准）；已评审/未评审都收（前者进对账、后者进 judge-only）
    - 叙述取最新版决定书 body（坐席改判版含人工文本，判它没有意义——
      仅取 issued_by=auto 的版本；无则跳过该案）
    """
    sample_rows = (
        (
            await session.execute(
                select(CaseEvent)
                .where(CaseEvent.kind == "narrative_sample")
                .order_by(CaseEvent.id.desc())
            )
        )
        .scalars()
        .all()
    )
    sampled: dict[str, CaseEvent] = {}
    for row in sample_rows:
        sampled.setdefault(row.case_id, row)  # id 降序 → 首见即最新

    humans: dict[str, dict[str, Any]] = {}
    for row in (
        (
            await session.execute(
                select(CaseEvent).where(CaseEvent.kind == "narrative_review")
            )
        )
        .scalars()
        .all()
    ):
        payload = row.payload if isinstance(row.payload, dict) else {}
        humans[row.case_id] = {
            "verdict": str(payload.get("verdict") or ""),
            "comment": str(payload.get("comment") or ""),
            "operator": str(payload.get("operator") or ""),
        }

    if not sampled:
        return []
    cases = {
        c.id: c
        for c in (
            await session.execute(select(Case).where(Case.id.in_(list(sampled))))
        ).scalars()
    }
    docs = (
        (
            await session.execute(
                select(DecisionDocument)
                .where(
                    DecisionDocument.case_id.in_(list(sampled)),
                    DecisionDocument.issued_by == "auto",
                )
                .order_by(DecisionDocument.case_id, DecisionDocument.version.desc())
            )
        )
        .scalars()
        .all()
    )
    latest_auto_doc: dict[str, DecisionDocument] = {}
    for doc in docs:
        latest_auto_doc.setdefault(doc.case_id, doc)

    samples: list[dict[str, Any]] = []
    for case_id in sorted(sampled):
        case = cases.get(case_id)
        doc = latest_auto_doc.get(case_id)
        if case is None or doc is None:
            continue
        samples.append(
            {
                "case_id": case_id,
                "case_type": case.case_type,
                "final_decision": case.final_decision,
                "approved_amount": str(case.approved_amount or ""),
                "narrative": doc.body,
                "human": humans.get(case_id),
            }
        )
    return samples


def build_judge_user_message(sample: dict[str, Any]) -> str:
    """judge 输入：案件结论事实 + 叙述正文，DATA 定界（T173 纪律同样适用）。"""
    facts = {
        "case_type": sample.get("case_type"),
        "final_decision": sample.get("final_decision"),
        "approved_amount": sample.get("approved_amount"),
    }
    return (
        "## 案件结论事实\n<<<DATA\n"
        + json.dumps(facts, ensure_ascii=False)
        + "\n>>>DATA\n\n## 决定书叙述正文\n<<<DATA\n"
        + str(sample.get("narrative") or "")
        + "\n>>>DATA"
    )


def parse_judge_response(content: str) -> dict[str, Any]:
    """judge 输出 → {三维分数, total, pass, rationale}；非法输出抛 ValueError。

    分数越界（非 0-2 整数）按非法处理——judge 纪律与结构化输出同款。
    """
    from tools.medical.ocr_extract import _parse_llm_json

    parsed = _parse_llm_json(content or "")
    if not isinstance(parsed, dict):
        msg = f"judge 输出非 JSON 对象: {str(content)[:120]}"
        raise ValueError(msg)
    scores: dict[str, int] = {}
    for dim in JUDGE_DIMENSIONS:
        value = parsed.get(dim)
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 2:
            msg = f"judge 维度 {dim} 分数非法: {value!r}"
            raise ValueError(msg)
        scores[dim] = value
    total = sum(scores.values())
    return {
        **scores,
        "total": total,
        "pass": total >= JUDGE_PASS_TOTAL,
        "rationale": str(parsed.get("rationale") or "")[:200],
    }


async def judge_narrative(sample: dict[str, Any]) -> dict[str, Any]:
    """单样本 judge（温度 0 单次判分；异常向上抛由调用方记 errors）。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    from services.llm.client import get_chat_model
    from services.llm.prompts import NARRATIVE_JUDGE_PROMPT
    from services.observability.token_tracker import phase_ainvoke

    model = get_chat_model(temperature=0.0)
    response = await phase_ainvoke(
        model,
        [
            SystemMessage(content=NARRATIVE_JUDGE_PROMPT),
            HumanMessage(content=build_judge_user_message(sample)),
        ],
        phase="narrative_judge",
    )
    return parse_judge_response(response.content or "")


def reconcile(
    judged: list[dict[str, Any]],
) -> dict[str, Any]:
    """judge 结果 × 人工 verdict 对账。

    - judged 元素形如 {"case_id", "human": {...}|None, "judge": {...}, "error"?: str}
    - 一致口径按案比较（pass/pass），不逐分比较（容忍 ±1 分抖动，D084）
    """
    errors = [j for j in judged if j.get("error")]
    valid = [j for j in judged if not j.get("error")]
    with_human = [j for j in valid if j.get("human")]
    judge_only = [j for j in valid if not j.get("human")]

    agreed = sum(
        1
        for j in with_human
        if bool(j["judge"]["pass"]) == (j["human"]["verdict"] == "pass")
    )
    distribution: dict[str, int] = {}
    for j in judge_only:
        key = str(j["judge"]["total"])
        distribution[key] = distribution.get(key, 0) + 1

    return {
        "judged": len(valid),
        "errors": len(errors),
        "reconciliation": {
            "judged_with_human": len(with_human),
            "agreed": agreed,
            "agreement_rate": round(agreed / len(with_human), 4) if with_human else None,
            "disagreements": [
                {
                    "case_id": j["case_id"],
                    "human_verdict": j["human"]["verdict"],
                    "human_comment": j["human"]["comment"],
                    "judge_total": j["judge"]["total"],
                    "judge_pass": j["judge"]["pass"],
                    "judge_rationale": j["judge"]["rationale"],
                }
                for j in with_human
                if bool(j["judge"]["pass"]) != (j["human"]["verdict"] == "pass")
            ],
        },
        "judge_only": {
            "count": len(judge_only),
            "distribution": dict(sorted(distribution.items())),
            # judge 的实际用途：低于阈值清单供坐席扩大抽评（不产生任何阻断）
            "below_threshold": [
                {
                    "case_id": j["case_id"],
                    "total": j["judge"]["total"],
                    "rationale": j["judge"]["rationale"],
                }
                for j in judge_only
                if not j["judge"]["pass"]
            ],
        },
        "error_details": [
            {"case_id": j["case_id"], "error": j["error"]} for j in errors
        ],
    }
