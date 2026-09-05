"""Intake 受理节点（F01）：险种分类 + 案件建档事件。

T079 桩版：按保单 product_type 确定性分类（保单是权威数据源）；T081 叠加 LLM 分类
（自由文本/无保单场景）。未上线险种（首批仅 medical pack）→ 受理期转人工（D039）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger
from schemas.case import CaseStatus
from services.case_store import CaseRecorder
from state import ClaimCaseState

log = get_logger(__name__)

# 保单产品类型 → 险种枚举（schemas.stages.InsuranceLine）
POLICY_TYPE_TO_LINE: dict[str, str] = {
    "医疗险": "medical",
    "意外险": "accident",
    "车险": "auto",
    "财产险": "property",
}
# 已上线 worker pack 的险种（D039：首批医疗险）
ONLINE_LINES: frozenset[str] = frozenset({"medical"})

PolicyLookup = Callable[[str], Awaitable[dict[str, Any] | None]]


def route_after_intake(state: ClaimCaseState) -> str:
    """intake 条件边：已上线险种 → orchestrator；未上线 → human_gate（受理转人工）。"""
    return "orchestrator" if state.get("case_type") in ONLINE_LINES else "human"


def make_intake_node(recorder: CaseRecorder, policy_lookup: PolicyLookup):
    """intake 节点工厂。policy_lookup(policy_no) -> {product_type, ...} | None。"""

    async def intake_node(state: ClaimCaseState) -> dict[str, Any]:
        info = await policy_lookup(state["policy_id"])  # 根据保单号查保单
        declared = state.get("declared_case_type")  # 获取用户声明的险种
        if info is not None:  # 保单存在
            case_type = POLICY_TYPE_TO_LINE.get(str(info.get("product_type", "")), "unknown")
        elif declared is not None:  # 保单不存在，但已声明险种
            case_type = declared
        else:  # 保单不存在，未声明险种
            case_type = "unknown"

        update: dict[str, Any] = {"case_type": case_type}  # 更新 case_type
        if case_type not in ONLINE_LINES:  # 未上线险种，转人工受理
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
