"""核赔合规门（F10，T085 实装）：三层审查 + 三态流转 + 修订闭环。

D039 安全设计 1：本节点在图上处于静态边（decision_generate → compliance_gate），
不在 orchestrator 调度空间——任何路由决策都无法绕过（图结构断言见测试）。

三层审查（纯函数，services.decision_doc.review_decision_document）：
1. 金额一致性断言（确定性）：正文核定金额 == 理算结果，不等 → MODIFY
   （revise 代码重渲染修复——金额来自模板注入，错误只可能来自叙述失控）
2. 红线规则（check_text）：违规承诺话术 → REJECT（对外文书零容忍，转人工）
3. PII 脱敏已在渲染层完成（services.decision_doc.render_decision_document）

修订闭环：MODIFY → revise_decision（代码重渲染，version+1 落库）→ 回合规复审；
轮数上限 compliance_max_rounds（默认 2）防死循环，超限 REJECT 转人工。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from services.case_store import CaseRecorder
from services.decision_doc import render_decision_document, review_decision_document
from state import ClaimCaseState

log = get_logger(__name__)


def compliance_route(state: ClaimCaseState) -> str:
    """合规三态条件边：PASS → 分级签发；MODIFY → 修订；REJECT → 转人工。"""
    compliance = state.get("compliance") or {}
    verdict = str(compliance.get("verdict", "PASS"))
    return {"PASS": "pass", "MODIFY": "modify", "REJECT": "reject"}.get(verdict, "pass")


def make_compliance_gate_node(recorder: CaseRecorder):
    """合规门节点工厂。"""

    async def compliance_gate_node(state: ClaimCaseState) -> dict[str, Any]:
        decision = state.get("decision") or {}
        calc = state.get("calc") or {}
        previous = state.get("compliance") or {}
        current_round = int(previous.get("round") or 1)

        review = review_decision_document(decision, calc)
        review["round"] = current_round

        # 轮数上限：MODIFY 修订超限 → REJECT 转人工（防死循环，D012 语义延续）
        if (
            review["verdict"] == "MODIFY"
            and current_round >= settings.compliance_max_rounds
        ):
            review["verdict"] = "REJECT"
            review["violations"].append(
                {"type": "max_rounds", "detail": f"修订已达上限 {settings.compliance_max_rounds} 轮"}
            )

        await recorder.event(
            state["case_id"],
            "stage_result",
            stage="compliance_gate",
            payload=review,
        )
        return {"compliance": review}

    return compliance_gate_node


def make_revise_decision_node(recorder: CaseRecorder):
    """修订节点：代码重渲染决定书（确定性修复，version+1 落库）→ 回合规复审。

    金额不一致的根因是叙述段失控——重渲染丢弃 LLM 叙述、改用规则版叙述
    （fallback_narrative），从源头消除再次不一致的可能。
    """

    async def revise_decision_node(state: ClaimCaseState) -> dict[str, Any]:
        decision = state.get("decision") or {}
        new_version = int(decision.get("version") or 1) + 1

        doc = render_decision_document(
            case_id=state["case_id"],
            case_type=str(state.get("case_type") or "unknown"),
            liability=state.get("liability") or {},
            calc=state.get("calc") or {},
            narrative=None,  # 丢弃叙述——规则版重渲染，确定性修复
            version=new_version,
            issued_by="auto",
        )
        await recorder.save_decision(
            state["case_id"],
            title=doc["title"],
            body=doc["body"],
            conclusion=doc["conclusion"],
            approved_amount=Decimal(str(doc["approved_amount"] or "0")),
        )
        log.info("decision_revised",
                 case_id=state["case_id"], version=new_version)
        return {"decision": doc}

    return revise_decision_node
