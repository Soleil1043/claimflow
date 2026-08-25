"""合规审查节点与三态流转（T018，F10）。

流程（decisions.md D012）：
1. 规则工具取证（rule_check + risk_scoring，经 ToolExecutor；
   工具不可用时回退纯函数，拦截能力恒在）
2. LLM 裁决（COMPLIANCE_AGENT_PROMPT + 工具证据）→ ComplianceAgentOutput；
   LLM 失败走确定性兜底：FRAUD_RISK 或 risk≥80 → REJECT；其他违规 → MODIFY；无违规 → PASS
3. 三态流转（条件边 compliance_route）：
   - PASS → END（回答原样返回）
   - MODIFY → revise_answer 节点（LLM 重写 + 正则兜底）→ 回 compliance 复审
     （compliance_rounds 上限 2，防死循环）
   - REJECT → END：final_answer 替换为安全话术（违规内容不返回用户、不落审计），
     need_human_intervention=True

图结构保证所有输出路径必经 compliance 节点（workflows/main_graph.py）。
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import get_logger
from schemas.agent_outputs import ComplianceAgentOutput, Violation
from services.llm.client import get_chat_model
from services.llm.prompts import (
    COMPLIANCE_AGENT_PROMPT,
    COMPLIANCE_REVIEW_PROMPT,
    REVISE_ANSWER_PROMPT,
)
from services.observability.llm_metrics import observed_ainvoke
from state import AgentState
from tools.compliance.risk_scoring import score_risk
from tools.compliance.rule_check import check_text
from tools.executor import ToolExecutor

log = get_logger(__name__)

# MODIFY 修订闭环的最大审查轮数（初审 + 修订后复审）
MAX_COMPLIANCE_ROUNDS = 2

# REJECT 后返回给用户的安全话术（原内容不返回）
REJECT_SAFE_MESSAGE = (
    "您咨询的内容涉及高风险事项，暂时无法通过智能客服为您解答，"
    "已为您转接人工服务，工作人员将尽快与您联系。"
)

# revise 节点 LLM 失败时的确定性替换（PROMISE/ABSOLUTE 高频话术）
_DETERMINISTIC_REPLACEMENTS: list[tuple[str, str]] = [
    ("保证赔付", "预估可赔付（最终以理赔审核结果为准）"),
    ("保证赔偿", "预估可赔偿（最终以理赔审核结果为准）"),
    ("保证理赔", "预估可理赔（最终以理赔审核结果为准）"),
    ("一定能赔", "预计可以申请理赔（最终以理赔审核结果为准）"),
    ("肯定能赔", "预计可以申请理赔（最终以理赔审核结果为准）"),
    ("肯定赔", "预计可以申请理赔（最终以理赔审核结果为准）"),
    ("绝对安全", "风险相对可控"),
    ("百分之百赔付", "按条款比例赔付"),
    ("百分之百报销", "按条款比例报销"),
    ("包赔", "按条款赔付"),
]


def _parse_llm_json(raw: str) -> dict[str, Any] | None:
    """解析 LLM 输出的 JSON（容忍 markdown 包裹/前后缀文本）。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def _run_rule_check(text: str, executor: ToolExecutor | None) -> list[dict[str, Any]]:
    """规则检查：优先 ToolExecutor（可观测），失败回退纯函数。"""
    if executor is not None:
        try:
            result = await executor.execute("compliance_rule_check", {"text": text})
            if result.success:
                return list(result.data.get("violations") or [])
            log.warning("rule_check_tool_failed", error=result.error_message)
        except Exception as exc:  # noqa: BLE001 工具不可用/执行异常 → 纯函数兜底
            log.warning("rule_check_tool_error", error=str(exc)[:200])
    return check_text(text)


async def _run_risk_scoring(
    text: str, violations: list[dict[str, Any]], executor: ToolExecutor | None
) -> dict[str, Any]:
    """风险评分：优先 ToolExecutor，失败回退纯函数。"""
    if executor is not None:
        try:
            result = await executor.execute("risk_scoring", {"text": text})
            if result.success:
                return dict(result.data)
            log.warning("risk_scoring_tool_failed", error=result.error_message)
        except Exception as exc:  # noqa: BLE001
            log.warning("risk_scoring_tool_error", error=str(exc)[:200])
    return score_risk(text, violations)


def _fallback_verdict(
    violations: list[dict[str, Any]], risk: dict[str, Any]
) -> ComplianceAgentOutput:
    """确定性兜底裁决（LLM 不可用时的保底拦截）。"""
    has_fraud = any(v.get("type") == "FRAUD_RISK" for v in violations)
    score = int(risk.get("risk_score", 0))
    if has_fraud or score >= 80:
        verdict = "REJECT"
        reason = f"规则兜底拦截：{'检出欺诈风险表述' if has_fraud else f'风险分 {score} ≥ 80'}"
    elif violations:
        verdict = "MODIFY"
        reason = f"规则兜底拦截：检出 {len(violations)} 处违规表述"
    else:
        verdict = "PASS"
        reason = "规则兜底放行：未检出违规"
    return ComplianceAgentOutput(
        verdict=verdict,
        violations=[Violation.model_validate(v) for v in violations],
        risk_score=score,
        reason=reason,
    )


async def review_answer(
    text: str, executor: ToolExecutor | None = None
) -> ComplianceAgentOutput:
    """审查一段拟返回用户的回答：工具取证 → LLM 裁决 → 确定性兜底。"""
    violations = await _run_rule_check(text, executor)
    risk = await _run_risk_scoring(text, violations, executor)

    evidence = json.dumps(
        {"violations": violations, "risk": risk}, ensure_ascii=False, default=str
    )
    try:
        model = get_chat_model(temperature=0.0)
        response = await observed_ainvoke(
            model,
            [
                SystemMessage(content=COMPLIANCE_AGENT_PROMPT),
                HumanMessage(content=COMPLIANCE_REVIEW_PROMPT.format(draft=text, evidence=evidence)),
            ],
        )
        parsed = _parse_llm_json(response.content or "")
        if parsed and parsed.get("verdict") in {"PASS", "MODIFY", "REJECT"}:
            output = ComplianceAgentOutput.model_validate(
                {
                    "verdict": parsed["verdict"],
                    "violations": parsed.get("violations") or [],
                    "risk_score": int(parsed.get("risk_score") or risk.get("risk_score", 0)),
                    "reason": str(parsed.get("reason", "")),
                }
            )
            log.info("compliance_reviewed", verdict=output.verdict, fallback=False)
            return output
        log.warning("compliance_llm_invalid_output", raw=(response.content or "")[:100])
    except Exception as exc:  # noqa: BLE001 LLM 故障 → 确定性兜底
        log.warning("compliance_llm_error", error=str(exc)[:200])

    verdict = _fallback_verdict(violations, risk)
    log.info("compliance_reviewed", verdict=verdict.verdict, fallback=True)
    return verdict


class ComplianceNode:
    """合规审查节点（有状态：绑定工具执行器）。"""

    def __init__(self, executor: ToolExecutor) -> None:
        self._executor = executor

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        draft = state.get("final_answer") or ""
        verdict = await review_answer(draft, self._executor)

        update: dict[str, Any] = {
            "compliance_result": verdict.model_dump(),
            "compliance_rounds": state.get("compliance_rounds", 0) + 1,
        }
        if verdict.verdict == "REJECT":
            # 违规内容不返回用户：替换为安全话术 + 标记转人工
            update["final_answer"] = REJECT_SAFE_MESSAGE
            update["need_human_intervention"] = True
            update["intervention_reason"] = f"合规审查拦截：{verdict.reason}"[:200]
        log.info(
            "compliance_node_done",
            verdict=verdict.verdict,
            rounds=update["compliance_rounds"],
            risk_score=verdict.risk_score,
        )
        return update


async def revise_answer_node(state: AgentState) -> dict[str, Any]:
    """MODIFY 修订节点：按合规建议重写回答（LLM 重写 + 正则兜底）。"""
    draft = state.get("final_answer") or ""
    compliance = state.get("compliance_result") or {}
    suggestions = "\n".join(
        f"- [{v.get('type')}] {v.get('suggestion') or v.get('detail')}"
        for v in compliance.get("violations") or []
    )

    revised = ""
    try:
        model = get_chat_model(temperature=0.0)
        response = await observed_ainvoke(
            model,
            [
                SystemMessage(content=REVISE_ANSWER_PROMPT.format(draft=draft, suggestions=suggestions)),
                HumanMessage(content="请输出修订后的回答。"),
            ],
        )
        revised = (response.content or "").strip()
    except Exception as exc:  # noqa: BLE001 LLM 故障 → 正则兜底
        log.warning("revise_llm_error", error=str(exc)[:200])

    if not revised:
        revised = _deterministic_revise(draft)
    log.info("answer_revised", length=len(revised))
    return {"final_answer": revised}


def _deterministic_revise(draft: str) -> str:
    """正则兜底修订：替换高频违规话术；无命中时追加合规提示。"""
    revised = draft
    for pattern, replacement in _DETERMINISTIC_REPLACEMENTS:
        revised = re.sub(pattern, replacement, revised)
    if revised == draft:
        revised += "\n（合规提示：以上内容仅供参考，最终以理赔审核结果为准。）"
    return revised


def compliance_route(state: AgentState) -> str:
    """条件边：三态流转。

    - REJECT → "reject"（END，介入标记已由节点写入）
    - MODIFY 且未达轮数上限 → "modify"（revise_answer → 回 compliance 复审）
    - 其余（PASS / MODIFY 达上限）→ "pass"（END）
    """
    verdict = (state.get("compliance_result") or {}).get("verdict", "PASS")
    rounds = state.get("compliance_rounds", 0)
    if verdict == "REJECT":
        return "reject"
    if verdict == "MODIFY" and rounds < MAX_COMPLIANCE_ROUNDS:
        return "modify"
    return "pass"
