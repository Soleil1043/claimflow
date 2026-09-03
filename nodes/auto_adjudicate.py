"""分级自动签发节点（F11）：配置阈值驱动的确定性决策（无 LLM）。

自动签发条件（全部满足）：责任 covered/partial 且 风险 low 且 核定金额 ≤ 自动签发线
且 置信度达标 → auto_issued；任一不满足 → 转人工复核（human_gate，kind=review）。
rejected（责任不成立）在风险低时同样自动出拒赔决定书。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.config import settings
from services.case_store import CaseRecorder
from state import ClaimCaseState


def adjudication_route(state: ClaimCaseState) -> str:
    """auto_adjudicate 条件边：human_request 已置 → 转人工；否则自动签发。"""
    return "human" if state.get("human_request") else "issue"


def make_auto_adjudicate_node(recorder: CaseRecorder):
    """分级签发节点工厂。"""

    async def auto_adjudicate_node(state: ClaimCaseState) -> dict[str, Any]:
        risk = state.get("risk") or {}
        liability = state.get("liability") or {}
        calc = state.get("calc") or {}
        material = state.get("material") or {}
        decision = state.get("decision") or {}

        approved = Decimal(str(calc.get("approved_amount") or "0"))
        not_covered = liability.get("verdict") == "not_covered"

        refer_reasons: list[str] = []
        if not not_covered and str(risk.get("risk_level", "low")) != "low":
            refer_reasons.append(f"风险等级 {risk.get('risk_level')}")
        if not not_covered and approved > settings.auto_approve_limit:
            refer_reasons.append(f"核定金额 {approved} 元超过自动签发线 {settings.auto_approve_limit} 元")
        min_confidence = min(
            float(material.get("confidence", 1.0)),
            float(liability.get("confidence", 1.0)),
        )
        if not not_covered and min_confidence < settings.auto_approve_confidence_floor:
            refer_reasons.append(f"链路置信度 {min_confidence:.2f} 低于门槛")

        if refer_reasons:
            await recorder.update_case(state["case_id"], status="referred")
            await recorder.event(
                state["case_id"],
                "status_change",
                payload={"status": "referred", "reasons": refer_reasons},
            )
            return {
                "human_request": {
                    "case_id": state["case_id"],
                    "kind": "review",
                    "reason": "；".join(refer_reasons),
                }
            }

        # 自动签发（rejected 拒赔书 / approved / partial）
        if not_covered:
            final_decision = "rejected"
            approved = Decimal("0.00")
        elif liability.get("verdict") == "partial":
            final_decision = "partial"
        else:
            final_decision = "approved"

        await recorder.update_case(
            state["case_id"],
            status="auto_issued",
            final_decision=final_decision,
            approved_amount=approved,
        )
        await recorder.event(
            state["case_id"],
            "status_change",
            payload={"status": "auto_issued", "final_decision": final_decision},
        )
        return {
            "final_decision": final_decision,
            "approved_amount": approved,
            "decision_document": decision or None,
        }
        # decision_document 已在 state（decision 阶段写入），此处显式透传保持 output 完整

    return auto_adjudicate_node
