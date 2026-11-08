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
from schemas.case import CaseStatus
from services.case_store import CaseRecorder
from services.decision_doc import render_decision_document
from services.memory.case_memory import write_case_memory
from services.observability import metrics
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
            # 既有正文复审（T205/I2）：合规门 REJECT 弹回的草稿含红线话术，confirm
            # 原样签发即违规文书外发。API 层已前置拦截（422 引导 rewrite，工单保持
            # 待处理）；此处为纵深兜底——经其他入口恢复时安全兜底 referred
            if check_text(str(decision.get("body") or "")):
                log.warning("agent_confirm_red_line_body", case_id=state["case_id"])
                return {"final_decision": "referred"}
            doc = {**decision, "issued_by": f"agent:{resolved_by}"}
        else:
            # 无既有决定书（高风险短路等未走完管线的案件）：按坐席决议渲染人工核定版。
            # 坐席 decision 是决定书结论值域（approved/rejected/partial），显式传
            # conclusion，不经 conclusion_for 的责任认定值域换算（T201/C1）
            conclusion = str(resolution.get("decision") or "approved")
            doc = render_decision_document(
                case_id=str(state["case_id"]),
                case_type=str(state.get("case_type") or "unknown"),
                liability={
                    "verdict": conclusion,
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
                conclusion=conclusion,
            )
        return {
            "final_decision": str(doc.get("conclusion") or "approved"),
            "decision_document": doc,
        }

    # rewrite：坐席改判/重写正文。坐席 decision 与既有决定书 conclusion 同属
    # 决定书结论值域，回退链全程不跨值域（T201/C1：旧实现把二者喂给
    # conclusion_for，"rejected" 翻转为 "approved"——改判拒赔与重写既有拒赔书
    # 都会被签发成核准赔付）
    base_decision = state.get("decision") or {}
    new_version = int(base_decision.get("version") or 0) + 1
    conclusion = str(resolution.get("decision") or base_decision.get("conclusion") or "approved")
    doc = render_decision_document(
        case_id=str(state["case_id"]),
        case_type=str(state.get("case_type") or "unknown"),
        liability={
            "verdict": conclusion,
            "reason": note or base_decision.get("reason") or "人工改判",
            "clause_references": base_decision.get("clause_references", []) or [],
        },
        calc={
            # 坐席给了金额（含 0）就用坐席的，未给才回退既有核定（T211/I8：
            # 判空用 is None——Command(resume) 直传的 Decimal("0") 是 falsy）
            "approved_amount": str(
                resolution["approved_amount"]
                if resolution.get("approved_amount") is not None
                else base_decision.get("approved_amount") or "0"
            ),
            "calculation_basis": "人工改判核定",
            "deductions": [],
        },
        narrative=None,
        version=new_version,
        issued_by=f"agent:{resolved_by}",
        conclusion=conclusion,
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


def _material_key(material: dict[str, Any]) -> Any:
    """材料去重键：落盘材料按 storage_path（每次上传唯一），引用类按（文件名, 类型）。"""
    return material.get("storage_path") or (material.get("file_name"), material.get("doc_type"))


def merge_materials(*sources: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """按源顺序合并材料并去重（先出现者保留）。"""
    seen: set[Any] = set()
    merged: list[dict[str, Any]] = []
    for source in sources:
        for material in source or []:
            key = _material_key(material)
            if key in seen:
                continue
            seen.add(key)
            merged.append(material)
    return merged


def make_human_gate_node(recorder: CaseRecorder, materials_lookup: Any = None):
    """human_gate 节点工厂。

    materials_lookup（T209/I6）：`async (case_id) -> list[dict]`，读 cases.materials
    （案件材料事实的单源，D047 口径）。注入时补件恢复以库内材料重建
    state.materials，并在再次补件挂起前回扫未纳入的已落库材料；None（测试/
    评测无 DB）时维持 state + 载荷合并的旧口径。
    """

    async def human_gate_node(state: ClaimCaseState) -> dict[str, Any]:
        request = state.get("human_request") or {}
        kind = str(request.get("kind", "review"))

        if kind == "supplement" and materials_lookup is not None:
            # 挂起前回扫（T209/I6）：恢复任务在飞期间（分钟级）的后续上传撞活跃任务
            # 唯一约束被静默吸收，材料已落库却不会再触发恢复。若库内已有本流程
            # 未纳入的材料，不挂起，直接带上重跑材料审核
            persisted = list(await materials_lookup(state["case_id"]) or [])
            known = {_material_key(m) for m in state.get("materials") or []}
            unseen = [m for m in persisted if _material_key(m) not in known]
            if unseen:
                metrics.record_supplement_rounds(1)
                log.info("supplement_rescan_resumed", case_id=state["case_id"],
                         unseen=len(unseen))
                return {
                    "human_request": None,
                    "human_resolution": {
                        "kind": "supplement",
                        "action": "rescan",
                        "added_materials": unseen,
                        "resolved_by": "material_rescan",
                    },
                    "materials": merge_materials(persisted, state.get("materials")),
                }

        status = (
            CaseStatus.SUPPLEMENT_PENDING if kind == "supplement" else CaseStatus.REFERRED
        )
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
            # 补件恢复埋点（T090 SUPPLEMENT_ROUNDS 接线，T099）：每次补件 observe 1，
            # 指标 sum = 补件恢复总次数
            metrics.record_supplement_rounds(1)
            added = list(resolution.get("added_materials") or [])
            # 以库内材料为单源重建（T209/I6）：恢复载荷只带触发恢复的那一份，
            # 同轮多文件的后续上传不在载荷里；库内 ∪ 既有 state ∪ 载荷（坐席登记的
            # 材料引用不落 cases.materials）按序去重
            persisted = (
                list(await materials_lookup(state["case_id"]) or [])
                if materials_lookup is not None
                else []
            )
            update["materials"] = merge_materials(persisted, state.get("materials"), added)
            return update

        if kind == "escape":
            update["final_decision"] = "referred"
            await recorder.update_case(
                state["case_id"], status=CaseStatus.REFERRED, final_decision="referred"
            )
            # 申请人记忆（T100）：升级线下处理终态
            await write_case_memory(
                state,
                outcome=CaseStatus.REFERRED,
                reason=str(request.get("reason") or "升级线下处理"),
            )
            return update

        # review：坐席复核（红线复审 → 签发/改判/安全兜底）
        review_update = resolve_review(state, resolution)
        update.update(review_update)
        if review_update.get("decision_document") is None:
            # 红线拦截安全兜底：不签发文书，终态转人工
            await recorder.update_case(
                state["case_id"], status=CaseStatus.REFERRED, final_decision="referred"
            )
            # 申请人记忆（T100）：REJECT 终态档案
            await write_case_memory(
                state,
                outcome=CaseStatus.REFERRED,
                reason="人工结论未过合规复审，安全兜底转人工",
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
                state["case_id"], status=CaseStatus.CLOSED,
                final_decision=str(issued.get("conclusion", "")),
                approved_amount=Decimal(str(issued.get("approved_amount") or "0")),
            )
            # 申请人记忆（T100）：坐席签发终态档案
            await write_case_memory(
                state,
                outcome=CaseStatus.CLOSED,
                final_decision=str(issued.get("conclusion", "")),
                approved_amount=Decimal(str(issued.get("approved_amount") or "0")),
            )
        return update

    return human_gate_node
