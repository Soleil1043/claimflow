"""人工介入门节点（F12，T086 完整闭环）：三类工单 interrupt 挂起与恢复。

工单类型与恢复语义：
- SUPPLEMENT（补件）：客户补传材料 → 重跑材料审核 → 静态边回 orchestrator 重规划
- REVIEW（核赔复核签批）：坐席 confirm（签发既有/按决议渲染决定书，issued_by=agent，
  版本化落库）或 rewrite（坐席重写正文）；坐席文本必过红线复审（T037 语义），
  违规不签发、安全兜底 referred
- ESCAPE（受理升级）：转专家线下处理，恢复即终态 referred

实现约定（T037 验证过的语义）：消费 interrupt 的节点返回普通 dict 更新，路由交给
条件边；human_request 消费即清场。
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from app.core.logging import get_logger
from services.case_store import CaseRecorder
from services.decision_doc import render_decision_document
from state import ClaimCaseState
from tools.compliance.rule_check import check_text

log = get_logger(__name__)


def human_gate_route(state: ClaimCaseState) -> str:
    """human_gate 条件边：补件恢复 → 重跑材料审核；签批/受理升级 → 终态。

    依据 human_resolution（本次恢复的落点）而非 human_request——后者在恢复时已被
    清空（消费即清场，否则遗留请求会让分级签发的条件边再次路由回本节点）。
    """
    if (state.get("human_resolution") or {}).get("kind") == "supplement":
        return "material_review"
    return "end"


def resolve_review(state: ClaimCaseState, resolution: dict[str, Any]) -> dict[str, Any]:
    """坐席复核决议 → 节点更新（纯函数，可独立单测）。

    决议形态：
    - {"action": "confirm"}：签发既有决定书（无决定书时按决议渲染人工核定版）
    - {"action": "rewrite", "body": ..., "decision": ..., "approved_amount": ...}：
      坐席改判/重写，渲染坐席版决定书
    - 坐席文本（note/body）必过红线复审（check_text）：违规不签发，安全兜底 referred
    """
    resolved_by = str(resolution.get("resolved_by") or "agent")
    note = str(resolution.get("note") or "")
    rewrite_body = str(resolution.get("body") or "")
    action = str(resolution.get("action") or ("rewrite" if rewrite_body else "confirm"))

    # 红线复审：坐席意见/正文不得含违规承诺话术（T037 语义）
    if check_text(note) or check_text(rewrite_body):
        log.warning("agent_resolution_red_line", case_id=state["case_id"])
        return {"final_decision": "referred"}

    if action == "confirm":
        decision = state.get("decision")
        if decision:
            doc = {**decision, "issued_by": f"agent:{resolved_by}"}
        else:
            # 无既有决定书（高风险短路等未走完管线的案件）：按坐席决议渲染人工核定版
            doc = render_decision_document(
                case_id=str(state["case_id"]),
                case_type=str(state.get("case_type") or "medical"),
                liability={
                    "verdict": str(resolution.get("decision") or "approved"),
                    "reason": note or "人工核定",
                    "clause_references": [],
                },
                calc={
                    "approved_amount": str(resolution.get("approved_amount") or "0"),
                    "calculation_basis": "人工核定",
                    "deductions": [],
                },
                narrative=None,
                version=int((state.get("decision") or {}).get("version") or 0) + 1,
                issued_by=f"agent:{resolved_by}",
            )
        return {
            "final_decision": str(doc.get("conclusion") or "approved"),
            "decision_document": doc,
        }

    # rewrite：坐席改判/重写正文
    base_decision = state.get("decision") or {}
    new_version = int(base_decision.get("version") or 0) + 1
    doc = render_decision_document(
        case_id=str(state["case_id"]),
        case_type=str(state.get("case_type") or "medical"),
        liability={
            "verdict": str(resolution.get("decision") or base_decision.get("conclusion") or "approved"),
            "reason": note or base_decision.get("reason") or "人工改判",
            "clause_references": base_decision.get("clause_references", []) or [],
        },
        calc={
            "approved_amount": str(resolution.get("approved_amount")
                                   or base_decision.get("approved_amount") or "0"),
            "calculation_basis": "人工改判核定",
            "deductions": [],
        },
        narrative=None,
        version=new_version,
        issued_by=f"agent:{resolved_by}",
    )
    if rewrite_body:
        # 坐席自拟正文（已过红线复审），保留坐席原文
        doc["body"] = mask_body(rewrite_body)
    return {
        "final_decision": str(doc.get("conclusion") or "approved"),
        "decision_document": doc,
    }


def mask_body(text: str) -> str:
    """坐席正文脱敏（复用渲染层同款脱敏，身份证/银行卡/手机号）。"""
    from tools.compliance.sensitive_filter import mask_sensitive

    return mask_sensitive(text)


def make_human_gate_node(recorder: CaseRecorder):
    """human_gate 节点工厂。"""

    async def human_gate_node(state: ClaimCaseState) -> dict[str, Any]:
        request = state.get("human_request") or {}
        kind = str(request.get("kind", "review"))
        status = "supplement_pending" if kind == "supplement" else "referred"
        await recorder.update_case(state["case_id"], status=status)

        resolution: Any = interrupt(
            {
                "case_id": state["case_id"],
                "kind": kind,
                "reason": request.get("reason"),
                "missing": request.get("missing", []),
                "message": "等待人工处理（Command(resume=...) 恢复）",
            }
        )
        if not isinstance(resolution, dict):
            resolution = {}

        update: dict[str, Any] = {
            "human_request": None,  # 消费即清场
            "human_resolution": {"kind": kind, **resolution},
        }

        if kind == "supplement":
            added = list(resolution.get("added_materials") or [])
            update["materials"] = list(state.get("materials") or []) + added
            return update

        if kind == "escape":
            update["final_decision"] = "referred"
            await recorder.update_case(
                state["case_id"], status="referred", final_decision="referred"
            )
            return update

        # review：坐席复核（红线复审 → 签发/改判/安全兜底）
        review_update = resolve_review(state, resolution)
        update.update(review_update)
        if review_update.get("decision_document") is None:
            # 红线拦截安全兜底：不签发文书，终态转人工
            await recorder.update_case(
                state["case_id"], status="referred", final_decision="referred"
            )
        issued = review_update.get("decision_document")
        if issued is not None:
            # 签发落库：坐席版决定书（版本化，issued_by=agent:<id>）
            from decimal import Decimal

            await recorder.save_decision(
                state["case_id"],
                title=str(issued.get("title", "")),
                body=str(issued.get("body", "")),
                conclusion=str(issued.get("conclusion", "")),
                approved_amount=Decimal(str(issued.get("approved_amount") or "0")),
                issued_by=str(issued.get("issued_by", "agent")),
            )
            await recorder.update_case(
                state["case_id"], status="closed",
                final_decision=str(issued.get("conclusion", "")),
                approved_amount=Decimal(str(issued.get("approved_amount") or "0")),
            )
        return update

    return human_gate_node
