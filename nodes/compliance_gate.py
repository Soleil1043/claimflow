"""核赔合规门（F10）——T079 桩版（T085 实装三态 + 金额一致性断言 + skill）。

D039 安全设计 1：本节点在图上处于静态边（decision_generate → compliance_gate），
不在 orchestrator 调度空间——任何路由决策都无法绕过（图结构断言见 T085/T079 测试）。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from services.case_store import CaseRecorder
from state import ClaimCaseState


def compliance_route(state: ClaimCaseState) -> str:
    """合规三态条件边（T079 桩：恒 PASS；T085 按裁决分流）。"""
    compliance = state.get("compliance") or {}
    verdict = str(compliance.get("verdict", "PASS"))
    return {"PASS": "pass", "MODIFY": "modify", "REJECT": "reject"}.get(verdict, "pass")


def make_compliance_gate_node(recorder: CaseRecorder):
    """合规门节点工厂（桩：直接放行，金额断言 T085 实装）。"""

    async def compliance_gate_node(state: ClaimCaseState) -> dict[str, Any]:
        decision = state.get("decision") or {}
        calc = state.get("calc") or {}
        # 桩版即预埋金额断言字段：T085 起不一致 → MODIFY/REJECT
        amount_consistent = Decimal(str(decision.get("approved_amount") or "0")) == Decimal(
            str(calc.get("approved_amount") or "0")
        )
        output = {
            "stage": "compliance_gate",
            "verdict": "PASS",
            "violations": [],
            "suggestion": None,
            "risk_score": 0.0,
            "amount_consistent": amount_consistent,
            "round": 1,
        }
        await recorder.event(
            state["case_id"], "stage_result", stage="compliance_gate", payload=output
        )
        return {"compliance": output}

    return compliance_gate_node


async def revise_decision_node(state: ClaimCaseState) -> dict[str, Any]:
    """修订节点桩（T085 实装：LLM 重写决定书后回合规复审）。"""
    return {}
