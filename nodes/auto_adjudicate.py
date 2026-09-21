"""分级自动签发节点（F11）：配置阈值驱动的确定性决策（无 LLM）。

自动签发条件（全部满足）：责任 covered/partial 且 风险 low 且 核定金额 ≤ 自动签发线
且 置信度达标 → auto_issued；任一不满足 → 转人工复核（human_gate，kind=review）。
rejected（责任不成立）在风险低时同样自动出拒赔决定书。
"""

from __future__ import annotations

import random
from decimal import Decimal
from typing import Any

from app.core.config import settings
from schemas.case import CaseStatus
from schemas.contract import final_decision_from_verdict
from services.case_store import CaseRecorder
from services.memory.case_memory import write_case_memory
from services.observability import metrics
from state import ClaimCaseState


def _narrative_sampled(case_id: str) -> bool:
    """叙述抽评确定性采样（T139，缺口#4）：random 种子=case_id——同案恒同结果，
    可复算可测试，不引入运行时随机性。"""
    return (
        random.Random(f"narrative-sample:{case_id}").random()
        < settings.manual_review_sample_rate
    )


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
            await recorder.update_case(state["case_id"], status=CaseStatus.REFERRED)
            await recorder.event(
                state["case_id"],
                "status_change",
                payload={"status": CaseStatus.REFERRED, "reasons": refer_reasons},
            )
            return {
                "human_request": {
                    "case_id": state["case_id"],
                    "kind": "review",
                    "reason": "；".join(refer_reasons),
                }
            }

        # 自动签发（终态判定单源 schemas.contract，T107；rejected 拒赔书 / approved / partial）
        if not_covered:
            approved = Decimal("0.00")
        final_decision = final_decision_from_verdict(
            str(liability.get("verdict") or "covered")
        )

        metrics.record_case_closed(
            case_type=str(state.get("case_type") or "unknown"),
            final_status=CaseStatus.AUTO_ISSUED,
        )
        metrics.record_decision_amount(float(approved))
        await recorder.update_case(
            state["case_id"],
            status=CaseStatus.AUTO_ISSUED,
            final_decision=final_decision,
            approved_amount=approved,
        )
        await recorder.event(
            state["case_id"],
            "status_change",
            payload={"status": CaseStatus.AUTO_ISSUED, "final_decision": final_decision},
        )
        # 叙述抽评采样（T139，缺口#4）：rate=0.05 首次接线——抽中落 narrative_sample
        # 事件进坐席评审队列（终态不动状态机；deterministic 采样见 _narrative_sampled）
        if settings.manual_review_sample_rate > 0 and _narrative_sampled(state["case_id"]):
            await recorder.event(
                state["case_id"],
                "narrative_sample",
                payload={"rate": settings.manual_review_sample_rate},
            )
        # 申请人记忆（T100）：签发终态档案；置信度=链路最低值（T138 门控：
        # 低于 memory_confidence_floor 不入档，低置信 not_covered 档案源头拦断）
        await write_case_memory(
            state,
            outcome=CaseStatus.AUTO_ISSUED,
            final_decision=final_decision,
            approved_amount=approved,
            confidence=min_confidence,
        )
        return {
            "final_decision": final_decision,
            "approved_amount": approved,
            "decision_document": decision or None,
        }
        # decision_document 已在 state（decision 阶段写入），此处显式透传保持 output 完整

    return auto_adjudicate_node
