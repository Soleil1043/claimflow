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

from dataclasses import dataclass, field
from typing import Any

from langgraph.types import Command

from app.core.config import settings
from app.core.logging import get_logger
from services.case_store import CaseRecorder
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


def make_orchestrator_node(recorder: CaseRecorder):
    """orchestrator 节点工厂：决策（T079=兜底编排）→ 守卫 → 审计 → 派发。"""

    async def orchestrator_node(state: ClaimCaseState) -> Command:
        calls = state.get("routing_calls", 0) + 1
        targets, human_request = default_route(state)

        if human_request is not None:
            verdict = GuardVerdict(targets=["human_gate"])
        else:
            verdict = enforce_guards(targets, state)

        if verdict.corrected:
            await recorder.event(
                state["case_id"],
                "guard_correction",
                payload={"requested": targets, **verdict.__dict__},
            )

        await recorder.event(
            state["case_id"],
            "routing",
            stage="orchestrator",
            payload={
                "calls": calls,
                "targets": verdict.targets,
                "corrected": verdict.corrected,
                "notes": verdict.notes,
                "mode": "deterministic_fallback",  # T081 起：llm / deterministic_fallback
            },
        )

        update: dict[str, Any] = {
            "routing_calls": calls,
            "task_plan": [{"stage": t, "status": "dispatched"} for t in verdict.targets],
        }
        if human_request is not None:
            update["human_request"] = {"case_id": state["case_id"], **human_request}

        return Command(update=update, goto=verdict.targets)

    return orchestrator_node
