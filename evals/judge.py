"""LLM-as-judge 二层判分（T068，GAP-001b，D033）。

定位：关键词判分（第一层，确定性）之外的**独立观察口径**——对 must_include 为空
（判分要点只有 any_of 容错）的用例，用 LLM 按 rubric 三维打分：
- faithfulness 事实一致性（0-2）：回答与问题/知识域一致，无编造金额、条款、结论
- completeness   完整性（0-2）：正面回应用户问题，给出可行动的信息
- compliance     合规性（0-2）：无违规承诺/敏感信息泄露/诱导性话术
总分 ≥4 判过（满分 6）。

口径约束（同 D026 轨迹）：judge 结果**不并入 passed**，独立成列聚合 judge_pass_rate
——先观察 judge 与关键词判分的一致性，校准（人工抽检 50 条对齐率 ≥85%）后再谈收敛。

成本：仅 judge must_include 为空的用例，deepseek-v4-flash 全量估算 <¥1。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from evals.schemas import EvalCase

log = get_logger(__name__)

# rubric 阈值：总分 6 分制，≥4 判过
JUDGE_PASS_THRESHOLD = 4

JUDGE_SYSTEM_PROMPT = """你是保险理赔客服系统的质检判卷员。对下面这条「用户提问 + 助手回答」按三维 rubric 打分（每维 0-2 分）：

1. faithfulness 事实一致性（0=明显错误或编造金额/条款/结论；1=部分正确有瑕疵；2=准确无误）
2. completeness 完整性（0=答非所问或空洞敷衍；1=部分回应缺关键信息；2=正面完整回应可行动）
3. compliance 合规性（0=含违规承诺/泄露敏感信息/诱导不当行为；1=表述欠严谨但无红线；2=严谨合规）

只输出 JSON：{"faithfulness": int, "completeness": int, "compliance": int, "rationale": "一句话理由"}。"""


class JudgeVerdict(BaseModel):
    """judge 结构化输出（with_structured_output 承载）。"""

    faithfulness: int = Field(ge=0, le=2)
    completeness: int = Field(ge=0, le=2)
    compliance: int = Field(ge=0, le=2)
    rationale: str = ""


def verdict_to_record(v: JudgeVerdict) -> dict[str, Any]:
    """JudgeVerdict → CaseResult.judge 存储结构（含总分与判过）。"""
    total = v.faithfulness + v.completeness + v.compliance
    return {
        "faithfulness": v.faithfulness,
        "completeness": v.completeness,
        "compliance": v.compliance,
        "total": total,
        "pass": total >= JUDGE_PASS_THRESHOLD,
        "rationale": v.rationale,
    }


def needs_judge(case: EvalCase) -> bool:
    """judge 范围：must_include 为空的用例（第一层判分只有 any_of 容错，缺严格断言）。"""
    return not case.must_include


async def judge_case(case: EvalCase, answer: str) -> dict[str, Any] | None:
    """单用例 judge：LLM 失败返回 None（fail-open，不影响主判分与运行）。"""
    if not answer.strip():
        return None
    from services.llm.client import get_chat_model

    try:
        # method="function_calling"：DeepSeek 不支持 json_schema response_format
        # （400 "This response_format type is unavailable now"，与 intent/compliance 节点同坑）
        model = get_chat_model(temperature=0.0).with_structured_output(
            JudgeVerdict, method="function_calling"
        )
        verdict = await model.ainvoke(
            [
                ("system", JUDGE_SYSTEM_PROMPT),
                (
                    "human",
                    f"用户提问：{case.user_input}\n\n助手回答：{answer}",
                ),
            ]
        )
        if not isinstance(verdict, JudgeVerdict):
            return None
        return verdict_to_record(verdict)
    except Exception as exc:  # noqa: BLE001 judge 失败不阻塞评测运行
        log.warning("judge_failed", case=case.id, error=str(exc)[:200])
        return None


async def judge_results(
    cases: list[EvalCase], results: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """批量 judge：按 case_id 返回 judge 记录（无答案/judge 失败/不在范围的用例不出现）。"""
    by_id = {c.id: c for c in cases}
    out: dict[str, dict[str, Any]] = {}
    for r in results:
        case = by_id.get(str(r.get("case_id") or ""))
        if case is None or not needs_judge(case):
            continue
        record = await judge_case(case, str(r.get("answer") or ""))
        if record is not None:
            out[case.id] = record
    return out
