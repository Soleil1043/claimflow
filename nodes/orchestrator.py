"""Orchestrator 调度节点（Phase 8 T079，D039）。

T079 为骨架版：路由决策走**确定性兜底编排**（default_route = 险种标准管线）；
T081 在同一切入点接入 LLM RoutingDecision（结构化输出），守卫与审计不变。

三层职责：
1. default_route——确定性兜底计划（险种标准管线，LLM 失败时的收敛保障）
2. enforce_guards——前置条件守卫（代码层纯函数：违规改投/去重/必做集/并行依赖）
3. orchestrator_node——决策 + 守卫 + 审计落 case_events + Command(goto) 派发
   （多目标 = 同一超步并行执行，fan-in 后本节点重跑；D039 静态合规门不受调度影响）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.messages import HumanMessage
from langgraph.graph import END
from langgraph.types import Send
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from services.case_store import CaseRecorder
from services.llm.client import get_chat_model
from services.llm.prompts import CASE_ORCHESTRATOR_ROUTING_PROMPT
from services.observability import metrics
from services.skills import build_system_prompt
from state import ClaimCaseState

log = get_logger(__name__)

# 阶段 worker 与其结论字段的映射（字段所有权表，v2 架构文档 5.3）
WORKER_STAGES: dict[str, str] = {
    "material_review": "material",
    "policy_verify": "policy",
    "fraud_check": "risk",
    "liability_judge": "liability",
    "amount_calc": "calc",
    "decision_generate": "decision",
}
# decision_generate（终局派发）的必做集
MUST_COMPLETE: tuple[str, ...] = (
    "material_review",
    "policy_verify",
    "fraud_check",
    "liability_judge",
    "amount_calc",
)

# LLM 可派发目标（"human" 转人工；其余为 worker 阶段）
RoutingTarget = Literal[
    "material_review",
    "policy_verify",
    "fraud_check",
    "liability_judge",
    "amount_calc",
    "decision_generate",
    "human",
]


class OrchestratorPlanStep(BaseModel):
    """LLM 计划单步（human 允许出现——转人工也是计划的一步）。"""

    stage: Literal[
        "material_review",
        "policy_verify",
        "fraud_check",
        "liability_judge",
        "amount_calc",
        "decision_generate",
        "human",
    ]
    description: str = ""


class RoutingDecision(BaseModel):
    """LLM orchestrator 结构化输出：本轮派发目标（可多个=并行）+ 计划 + 理由。"""

    next: list[RoutingTarget]
    plan: list[OrchestratorPlanStep] = Field(default_factory=list)
    reason: str = ""


@dataclass
class GuardVerdict:
    """守卫裁决：修正后的派发目标 + 是否修正 + 修正说明（审计用）。"""

    targets: list[str] = field(default_factory=list)
    corrected: bool = False
    notes: list[str] = field(default_factory=list)


def stage_done(state: ClaimCaseState, worker: str) -> bool:
    """该 worker 的阶段结论是否已产出。"""
    return state.get(WORKER_STAGES[worker]) is not None


def _material_complete(state: ClaimCaseState) -> bool:
    material = state.get("material")
    return material is not None and material.get("completeness") == "complete"


def _missing_prerequisite(worker: str, state: ClaimCaseState) -> str | None:
    """返回该 worker 缺失的前置 worker（应改投目标）；None=可派发；"__drop__"=无前置可补（丢弃）。"""
    if worker == "material_review":
        return None
    if worker in ("policy_verify", "fraud_check"):
        if state.get("material") is None:
            return "material_review"
        if not _material_complete(state):
            return "__drop__"  # 材料残缺需人工补件，重跑材料审核无意义
        return None
    if worker == "liability_judge":
        if state.get("policy") is None:
            return "policy_verify"
        if state.get("risk") is None:
            return "fraud_check"
        return None
    if worker == "amount_calc":
        if state.get("liability") is None:
            return "liability_judge"
        return None
    if worker == "decision_generate":
        for must in MUST_COMPLETE:
            if not stage_done(state, must):
                return must
        return None
    return None


def enforce_guards(targets: list[str], state: ClaimCaseState) -> GuardVerdict:
    """前置条件守卫（纯函数，D039 安全设计 2）。

    规则：
    - 前置缺失 → 改投缺失的前置 worker（违规改投）
    - 材料残缺时查询类 worker 无前置可补 → 丢弃（等补件走 human 通道）
    - 重派已完成 worker → 丢弃（去重）；补件恢复（human_resolution 在）时允许重跑材料审核
    - decision_generate 必须单派，且必做集未全 done 时改投首个缺失项
    - 并行批次内的依赖冲突由"前置改投 + 去重"自然消解
    """
    notes: list[str] = []
    corrected = False
    result: list[str] = []
    rerun_allowed = state.get("human_resolution") is not None

    for target in targets:
        if target not in WORKER_STAGES:
            result.append(target)  # human_gate 等非 worker 目标透传
            continue
        if stage_done(state, target) and not (target == "material_review" and rerun_allowed):
            notes.append(f"drop_done:{target}")
            corrected = True
            continue
        missing = _missing_prerequisite(target, state)
        if missing == "__drop__":
            notes.append(f"drop_blocked:{target}")
            corrected = True
            continue
        if missing is not None:
            notes.append(f"redirect:{target}->{missing}")
            corrected = True
            if missing not in result:
                result.append(missing)
            continue
        if target not in result:
            result.append(target)

    # decision_generate 必须单派：与其他目标同批时移除（等下一轮终局派发）
    if "decision_generate" in result and len(result) > 1:
        result.remove("decision_generate")
        notes.append("defer_decision_generate:solo_only")
        corrected = True

    return GuardVerdict(targets=result, corrected=corrected, notes=notes)


def default_route(state: ClaimCaseState) -> tuple[list[str], dict[str, Any] | None]:
    """确定性兜底编排：险种标准管线（医疗险：材料→保单∥风控→责任→理算→决定书）。

    返回 (派发目标, 人工介入载荷|None)。T081 LLM 决策失败时回退到本函数（D039 安全设计 3）。
    """
    material = state.get("material")
    if material is None:
        return ["material_review"], None
    if material.get("completeness") == "partial":
        return ["human_gate"], {
            "kind": "supplement",
            "reason": "材料不全，等待客户补件",
            "missing": material.get("missing", []),
        }
    if material.get("confidence", 0.0) < settings.material_confidence_floor:
        return ["human_gate"], {
            "kind": "review",
            "reason": "材料抽取置信度低或材料自相矛盾，转人工裁量",
        }

    policy = state.get("policy")
    risk = state.get("risk")
    if policy is None and risk is None:
        return ["policy_verify", "fraud_check"], None  # 相互独立，同超步并行
    if policy is None:
        return ["policy_verify"], None
    if risk is None:
        return ["fraud_check"], None
    if risk.get("risk_level") == "high":
        return ["human_gate"], {"kind": "review", "reason": "高风险短路，转人工核赔"}

    if state.get("liability") is None:
        return ["liability_judge"], None
    if state.get("calc") is None:
        return ["amount_calc"], None
    if state.get("decision") is None:
        return ["decision_generate"], None

    # 决定书之后由静态合规链接管，正常不会到达；防御性转人工
    return ["human_gate"], {"kind": "review", "reason": "流程未收敛，转人工核查"}


def _stage_snapshot(state: ClaimCaseState) -> dict[str, Any]:
    """案件快照（LLM 路由的观察输入）：各阶段执行状态 + 关键事实摘要。"""

    def fact(channel: str, keys: tuple[str, ...]) -> dict[str, Any] | None:
        data = state.get(channel)
        if not isinstance(data, dict):
            return None
        return {k: data.get(k) for k in keys}

    errors = state.get("errors") or []
    return {
        "case": {
            "case_type": state.get("case_type"),
            "claimed_amount": str(state.get("claimed_amount", "")),
            "incident_description": str(state.get("incident_description", ""))[:200],
        },
        "stages": {
            "material_review": fact("material", ("completeness", "missing", "confidence")),
            "policy_verify": fact(
                "policy", ("coverage_valid", "waiting_period_passed", "invalid_reason")
            ),
            "fraud_check": fact("risk", ("risk_level", "risk_score")),
            "liability_judge": fact("liability", ("verdict", "confidence")),
            "amount_calc": fact("calc", ("approved_amount",)),
            "decision_generate": "done" if state.get("decision") is not None else None,
        },
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
        system = build_system_prompt(
            CASE_ORCHESTRATOR_ROUTING_PROMPT,
            "orchestrator",
            state.get("case_type") or "_shared",
            snapshot=json.dumps(_stage_snapshot(state), ensure_ascii=False, default=str)[:3000],
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
    条件边以 Send 实现（文档化 map-reduce 语义）——节点内 Command(goto=[Send..])
    实测会把非首目标以错误入参调用（T079 踩坑实录）。
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
