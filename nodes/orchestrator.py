"""Orchestrator 调度节点（Phase 8 T079，D039；T118 守卫层拆出 nodes/guards.py）。

本模块 = 调度装配层：
- RoutingDecision——LLM 结构化路由决策 schema（make_llm_router 装配 skill + 快照）
- orchestrator_node——决策（LLM 优先，失败/超预算回退确定性兜底）→ 守卫修正 →
  审计落 case_events → 写 pending_dispatch（普通 dict 返回）
- route_dispatch——条件边按派发目标 Send 并行（文档化 map-reduce 范式；
  节点内 Command(goto=[Send..]) 实测会把非首目标以错误入参调用，T079 踩坑实录）

确定性裁决（enforce_guards / default_route / GuardVerdict / stage_done）见 nodes/guards.py。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.graph import END
from langgraph.types import Send
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from nodes.guards import GuardVerdict, default_route, enforce_guards
from schemas.stages import STAGE_SPECS, DispatchTarget
from services.case_store import CaseRecorder
from services.llm.client import get_chat_model
from services.llm.prompts import CASE_ORCHESTRATOR_ROUTING_PROMPT
from services.observability import metrics
from services.observability.tracing import traced_span
from services.skills import build_system_prompt
from state import ClaimCaseState

log = get_logger(__name__)


class OrchestratorPlanStep(BaseModel):
    """LLM 计划单步（human 允许出现——转人工也是计划的一步）。"""

    stage: DispatchTarget
    description: str = ""


class RoutingDecision(BaseModel):
    """LLM orchestrator 结构化输出：本轮派发目标（可多个=并行）+ 计划 + 理由。"""

    next: list[DispatchTarget]
    plan: list[OrchestratorPlanStep] = Field(default_factory=list)
    reason: str = ""


def _stage_snapshot(state: ClaimCaseState) -> dict[str, Any]:
    """案件快照（LLM 路由的观察输入）：各阶段执行状态 + 关键事实摘要。

    阶段条目由 StageSpec.snapshot_keys 派生（D040）；无 snapshot_keys 的阶段
    （decision_generate）仅显示 done/None。
    """
    errors = state.get("errors") or []
    stages: dict[str, Any] = {}
    for spec in STAGE_SPECS:
        data = state.get(spec.channel)
        if not spec.snapshot_keys:
            stages[spec.name] = "done" if data is not None else None
        elif isinstance(data, dict):
            stages[spec.name] = {k: data.get(k) for k in spec.snapshot_keys}
        else:
            stages[spec.name] = None
    return {
        "case": {
            "case_type": state.get("case_type"),
            "claimed_amount": str(state.get("claimed_amount", "")),
            "incident_description": str(state.get("incident_description", ""))[:200],
        },
        "stages": stages,
        "recent_errors": [
            str(e.get("message", e))[:120] for e in errors[-3:] if isinstance(e, dict)
        ],
    }


def make_llm_router():
    """LLM 路由器工厂（默认路由器，D039）。

    - 装载调度 skill（skills/orchestrator/<险种>.md → _shared.md 回退）拼入提示词
    - RoutingDecision 结构化输出（function_calling，DeepSeek 口径同 D022/T068）
    - settings.orchestrator_llm_enabled=False 时返回 None（纯确定性编排，测试/降级用）
    - 调用侧对异常一律回退 default_route，本函数不吞错（让调用方感知失败并审计）
    """
    if not settings.orchestrator_llm_enabled:
        return None

    async def llm_router(state: ClaimCaseState) -> RoutingDecision:
        snapshot_data = _stage_snapshot(state)
        if settings.memory_in_routing:
            # 申请人历史档案注入（T100，默认关）：LLM 模式增强，确定性评测不受影响
            from services.memory.case_memory import (
                format_case_memories,
                search_case_memories,
            )

            records = await search_case_memories(str(state.get("user_id") or ""))
            if records:
                snapshot_data["applicant_history"] = format_case_memories(records)
        system = build_system_prompt(
            CASE_ORCHESTRATOR_ROUTING_PROMPT,
            "orchestrator",
            state.get("case_type") or "_shared",
            snapshot=json.dumps(snapshot_data, ensure_ascii=False, default=str)[:3000],
        )
        model = get_chat_model(temperature=0.0)
        structured = model.with_structured_output(RoutingDecision, method="function_calling")
        return await structured.ainvoke([HumanMessage(content=system)])

    return llm_router


def make_orchestrator_node(recorder: CaseRecorder, llm_router=None):
    """orchestrator 节点工厂：决策 → 守卫 → 审计 → 写派发目标。

    llm_router=None 时为纯确定性兜底编排（T079 形态）；传入路由器后优先 LLM 决策
    （D039），失败/超预算回退 default_route。安全对冲不变：
    - 前置条件守卫 enforce_guards（代码层，违规改投）
    - decision_generate 单派 + 必做集
    - 决策审计（mode/reason/守卫修正）落 case_events
    节点只写 `pending_dispatch`（普通 dict 返回）；并行派发由 route_dispatch
    条件边以 Send 实现。
    """

    async def orchestrator_node(state: ClaimCaseState) -> dict[str, Any]:
        calls = state.get("routing_calls", 0) + 1
        over_budget = calls > settings.routing_call_budget
        mode = "deterministic_fallback"
        decision: RoutingDecision | None = None
        human_request: dict[str, Any] | None = None
        requested: list[str] = []

        if llm_router is not None and not over_budget:
            try:
                with traced_span(
                    "case.orchestrator_route",
                    case_id=state.get("case_id"), routing_call=calls,
                ):
                    decision = await llm_router(state)
                mode = "llm"
            except Exception as exc:  # noqa: BLE001 —— D039 安全设计 3：LLM 故障走兜底
                log.warning("orchestrator_llm_failed", calls=calls, error=str(exc)[:200])
                metrics.record_orch_fallback()
                decision = None
        elif over_budget:
            log.warning("orchestrator_over_budget", calls=calls)

        if decision is not None:
            requested = [str(t) for t in decision.next]
            if "human" in requested:
                # kind 由代码按机械事实判定（LLM 只决定"要不要人"）：
                # 材料残缺 → 补件（supplement）；其余 → 复核（review）
                material = state.get("material") or {}
                if material.get("completeness") == "partial":
                    human_request = {
                        "kind": "supplement",
                        "reason": "材料不全，等待客户补件",
                        "missing": material.get("missing", []),
                    }
                else:
                    human_request = {
                        "kind": "review",
                        "reason": decision.reason or "orchestrator 裁量转人工",
                    }
            targets = [t for t in requested if t != "human"]
            if human_request is not None:
                verdict = GuardVerdict(targets=["human_gate"])
            else:
                verdict = enforce_guards(targets, state)
        else:
            targets, human_request = default_route(state)
            requested = list(targets)
            if human_request is not None:
                verdict = GuardVerdict(targets=["human_gate"])
            else:
                verdict = enforce_guards(targets, state)

        if verdict.corrected:
            metrics.record_guard_correction()
            await recorder.event(
                state["case_id"],
                "guard_correction",
                payload={"requested": requested, **verdict.__dict__},
            )

        metrics.record_routing_calls(calls)
        await recorder.event(
            state["case_id"],
            "routing",
            stage="orchestrator",
            payload={
                "calls": calls,
                "mode": mode,
                "over_budget": over_budget,
                "requested": requested,
                "targets": verdict.targets,
                "corrected": verdict.corrected,
                "notes": verdict.notes,
                "reason": (decision.reason[:200] if decision is not None else None),
            },
        )

        plan = (
            [{"stage": s.stage, "status": "dispatched", "description": s.description}
             for s in decision.plan]
            if decision is not None and decision.plan
            else [{"stage": t, "status": "dispatched"} for t in verdict.targets]
        )
        update: dict[str, Any] = {
            "routing_calls": calls,
            "task_plan": plan,
            "pending_dispatch": None if human_request is not None else verdict.targets,
        }
        if human_request is not None:
            update["human_request"] = {"case_id": state["case_id"], **human_request}
        return update

    return orchestrator_node


def route_dispatch(state: ClaimCaseState) -> str | list[Send]:
    """orchestrator 条件边：人工介入 → human_gate；否则按派发目标 Send 并行。

    条件边函数返回 Send 列表是 langgraph 文档化的 fan-out 范式；载荷 = 当前完整
    state——worker 按共享状态语义读写，写入经 channel 合并回主图。
    """
    if state.get("human_request"):
        return "human_gate"
    targets = state.get("pending_dispatch") or []
    if not targets:
        # 防御：无目标可派（正常流程不会到达）
        return END
    return [Send(w, state) for w in targets]
