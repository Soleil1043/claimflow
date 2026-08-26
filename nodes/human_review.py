"""人工介入审核节点（T037）：REJECT 路径的 LangGraph interrupt 挂起与恢复。

流程：
1. compliance 节点 REJECT → 路由到本节点 → 调用 interrupt(payload) 挂起图
   （payload 含拦截原因与合规裁决快照；checkpoint 持久化挂起状态，跨服务重启可恢复）
2. 坐席在 HITL 工单侧 resolve → 后端以 Command(resume={"resolution_note", "resolved_by"})
   恢复 → 本节点重跑，interrupt() 直接返回坐席结论
3. 坐席结论经合规复审（review_answer，防坐席文本本身违规）：
   - PASS / MODIFY → 结论作为 final_answer 返回用户（转人工闭环）
   - REJECT → 保守安全话术（结论不返回，转人工状态维持）

节点必须独立于 compliance 节点：interrupt 恢复时整个节点重跑，
放 compliance 内会导致 LLM 审查重复执行且结果漂移。
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from app.core.logging import get_logger
from nodes.compliance import REJECT_SAFE_MESSAGE, review_answer
from state import AgentState
from tools.executor import ToolExecutor

log = get_logger(__name__)


class HumanReviewNode:
    """人工审核节点（有状态：绑定工具执行器供复审取证）。"""

    def __init__(self, executor: ToolExecutor) -> None:
        self._executor = executor

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        # 首次执行在此挂起；坐席恢复时重跑本节点，interrupt() 直接返回 resume 值
        decision = interrupt(
            {
                "intervention_reason": state.get("intervention_reason"),
                "compliance_result": state.get("compliance_result"),
                "message": "合规审查 REJECT，等待坐席处理（resolution_note 回写结论）",
            }
        )
        log.info("human_review_resumed", payload_type=type(decision).__name__)

        if not isinstance(decision, dict):
            log.warning("human_review_invalid_resume", decision=str(decision)[:100])
            return {"final_answer": REJECT_SAFE_MESSAGE}

        note = str(decision.get("resolution_note") or "").strip()
        if not note:
            log.warning("human_review_empty_note")
            return {"final_answer": REJECT_SAFE_MESSAGE}

        # 坐席结论同样过合规门禁（F10：所有返回用户的内容必经审查）
        verdict = await review_answer(note, self._executor)
        if verdict.verdict == "REJECT":
            log.warning(
                "human_review_note_rejected", risk_score=verdict.risk_score, reason=verdict.reason
            )
            return {
                "final_answer": (
                    "人工坐席已处理您的问题，但回复内容仍在合规复核中，"
                    "请稍后通过官方渠道查看处理结果。"
                ),
                "compliance_result": verdict.model_dump(),
            }

        log.info(
            "human_review_note_passed",
            verdict=verdict.verdict,
            resolved_by=decision.get("resolved_by"),
        )
        # 介入闭环：结论（复审通过）返回用户
        return {
            "final_answer": note,
            "need_human_intervention": False,
            "compliance_result": verdict.model_dump(),
        }
