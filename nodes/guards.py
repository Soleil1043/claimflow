"""调度裁决纯函数层（自 nodes/orchestrator.py 拆出，T118）。

两件确定性决策（零 LLM，D039 安全设计的代码层）：
1. enforce_guards——前置条件守卫：违规改投 / 去重 / 必做集 / 并行依赖消解
2. default_route——确定性兜底编排：险种标准管线（LLM 失败时的收敛保障）

数据来自 schemas.stages 注册表（D040：前置清单是数据，守卫算法是代码）；
材料完整性强制（_COMPLETION_GATED）是守卫行为数据，保持在本模块。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from schemas.stages import (
    MUST_COMPLETE,
    STAGE_CHANNELS,
    STAGE_SPECS_BY_NAME,
    DispatchTarget,
)
from state import ClaimCaseState

# 材料完整性强制的查询类 worker：材料存在但残缺时无前置可补 → 丢弃（等补件走 human 通道）。
# 这是守卫行为数据（D040：算法保持代码），不进 StageSpec。
_COMPLETION_GATED: tuple[DispatchTarget, ...] = (
    DispatchTarget.POLICY_VERIFY,
    DispatchTarget.FRAUD_CHECK,
)


@dataclass
class GuardVerdict:
    """守卫裁决：修正后的派发目标 + 是否修正 + 修正说明（审计用）。"""

    targets: list[str] = field(default_factory=list)
    corrected: bool = False
    notes: list[str] = field(default_factory=list)


def stage_done(state: ClaimCaseState, worker: str) -> bool:
    """该 worker 的阶段结论是否已产出。"""
    return state.get(STAGE_CHANNELS[worker]) is not None


def _material_complete(state: ClaimCaseState) -> bool:
    material = state.get("material")
    return material is not None and material.get("completeness") == "complete"


def _missing_prerequisite(worker: str, state: ClaimCaseState) -> str | None:
    """返回该 worker 缺失的前置 worker（应改投目标）；None=可派发；"__drop__"=无前置可补（丢弃）。

    前置清单来自 StageSpec.requires（D040）；材料完整性与必做集语义保持代码。
    """
    spec = STAGE_SPECS_BY_NAME[worker]
    if spec.name is DispatchTarget.MATERIAL_REVIEW:
        return None
    for req in spec.requires:
        if not stage_done(state, req):
            return req
    if spec.name in _COMPLETION_GATED and not _material_complete(state):
        return "__drop__"  # 材料残缺需人工补件，重跑材料审核无意义
    if spec.name is DispatchTarget.DECISION_GENERATE:
        for must in MUST_COMPLETE:
            if not stage_done(state, must):
                return must
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
        if target not in STAGE_CHANNELS:
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

    返回 (派发目标, 人工介入载荷|None)。LLM 决策失败时回退到本函数（D039 安全设计 3）。
    """
    material = state.get("material")
    if material is None:
        return [DispatchTarget.MATERIAL_REVIEW], None
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
        # 相互独立，同超步并行
        return [DispatchTarget.POLICY_VERIFY, DispatchTarget.FRAUD_CHECK], None
    if policy is None:
        return [DispatchTarget.POLICY_VERIFY], None
    if risk is None:
        return [DispatchTarget.FRAUD_CHECK], None
    if risk.get("risk_level") == "high":
        return ["human_gate"], {"kind": "review", "reason": "高风险短路，转人工核赔"}

    if state.get("liability") is None:
        return [DispatchTarget.LIABILITY_JUDGE], None
    if state.get("calc") is None:
        return [DispatchTarget.AMOUNT_CALC], None
    if state.get("decision") is None:
        return [DispatchTarget.DECISION_GENERATE], None

    # 决定书之后由静态合规链接管，正常不会到达；防御性转人工
    return ["human_gate"], {"kind": "review", "reason": "流程未收敛，转人工核查"}
