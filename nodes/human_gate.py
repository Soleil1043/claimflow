"""人工介入门节点（F12 前置版，T086 完善三类工单闭环）。

interrupt 挂起（checkpoint 持久化，跨重启可恢复）；坐席/客户以 Command(resume=...) 恢复：
- supplement（补件）：合并补传材料 → 条件边路由回材料审核（完成后经静态边回 orchestrator 重规划）
- review/escape（签批/受理升级）：T079 最小闭环——恢复即转人工终态；T086 接坐席结论
  + 合规复审 + 决定书签发

实现约定（T037 验证过的语义）：消费 interrupt 的节点返回普通 dict 更新，路由交给
条件边——interrupt 后立刻返回 Command(goto) 会让挂起任务记录残留在 checkpoint。
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from services.case_store import CaseRecorder
from state import ClaimCaseState


def human_gate_route(state: ClaimCaseState) -> str:
    """human_gate 条件边：补件恢复 → 重跑材料审核；签批/受理升级 → 终态。

    依据 human_resolution（本次恢复的落点）而非 human_request——后者在恢复时已被
    清空（消费即清场，否则遗留请求会让分级签发的条件边再次路由回本节点）。
    """
    if (state.get("human_resolution") or {}).get("kind") == "supplement":
        return "material_review"
    return "end"


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
            # 消费即清场：遗留的 human_request 会让下游条件边（分级签发）误判仍在转人工
            "human_request": None,
            "human_resolution": {"kind": kind, **resolution},
        }
        if kind == "supplement":
            added = list(resolution.get("added_materials") or [])
            update["materials"] = list(state.get("materials") or []) + added
        else:
            # T079 最小闭环：转人工终态；T086 换成坐席结论 + 合规复审 + 决定书签发
            update["final_decision"] = "referred"
        return update

    return human_gate_node
