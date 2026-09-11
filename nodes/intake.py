"""Intake 受理节点（F01）：险种分类 + 案件建档事件。

按保单 product_type 确定性分类（保单是权威数据源）；无保单回退客户自报险种
（declared_case_type），均缺失 → unknown。未上线险种（首批仅 medical pack）→
受理期转人工（D039）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger
from schemas.case import CaseStatus
from schemas.lines import online_lines, pack_for_product_type
from services.case_store import CaseRecorder
from state import ClaimCaseState

log = get_logger(__name__)

PolicyLookup = Callable[[str], Awaitable[dict[str, Any] | None]]


def route_after_intake(state: ClaimCaseState) -> str:
    """intake 条件边：已上线险种 → orchestrator；未上线 → human_gate（受理转人工）。"""
    return "orchestrator" if state.get("case_type") in online_lines() else "human"


def make_intake_node(recorder: CaseRecorder, policy_lookup: PolicyLookup):
    """intake 节点工厂。policy_lookup(policy_no) -> {product_type, ...} | None。"""

    async def intake_node(state: ClaimCaseState) -> dict[str, Any]:
        info = await policy_lookup(state["policy_id"])  # 根据保单号查保单
        declared = state.get("declared_case_type")  # 获取用户声明的险种
        if info is not None:  # 保单存在：产品类型 → 险种 pack 归属
            pack = pack_for_product_type(str(info.get("product_type", "")))
            case_type = pack.line if pack is not None else "unknown"
        elif declared is not None:  # 保单不存在，但已声明险种
            case_type = declared
        else:  # 保单不存在，未声明险种
            case_type = "unknown"

        update: dict[str, Any] = {"case_type": case_type}  # 更新 case_type
        if case_type not in online_lines():  # 未上线险种，转人工受理
            update["human_request"] = {
                "case_id": state["case_id"],
                "kind": "escape",
                "reason": f"险种「{case_type}」的自动核赔能力未上线，转人工受理",
            }
            log.info("intake_referred_offline", case_id=state["case_id"], case_type=case_type)

        # 更新案件状态为 in_progress
        await recorder.update_case(
            state["case_id"], case_type=case_type, status=CaseStatus.IN_PROGRESS
        )
        # 记录 intake 结果
        await recorder.event(
            state["case_id"],
            "stage_result",
            stage="intake",
            payload={"case_type": case_type, "policy_found": info is not None},
        )
        return update

    return intake_node
