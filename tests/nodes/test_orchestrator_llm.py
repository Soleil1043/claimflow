"""LLM Orchestrator 单元测试（T081，D039）。

用脚本化路由器（不调真实 LLM）验证节点集成语义：
- LLM 非法目标 → 守卫 100% 改投（guard_correction 审计）
- LLM 失败 → 回退确定性兜底（mode=deterministic_fallback）
- 超预算 → 不再调用 LLM，强制兜底
- human 裁量 → human_request 载荷
并行派发并发断言与兜底 e2e 见 tests/workflows/test_orchestrator_llm.py。
"""

from __future__ import annotations

from typing import Any

from nodes.orchestrator import RoutingDecision, make_orchestrator_node


class MiniRecorder:
    """内存审计记录器。"""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []

    async def event(self, case_id, kind, stage=None, payload=None) -> None:
        self.events.append(
            {"case_id": case_id, "kind": kind, "stage": stage, "payload": payload}
        )

    async def update_case(self, case_id, **kwargs) -> None:
        self.updates.append({"case_id": case_id, **kwargs})

    async def save_decision(self, case_id, **kwargs) -> None:
        self.decisions.append({"case_id": case_id, **kwargs})


async def test_llm_illegal_target_intercepted_by_guard() -> None:
    """LLM 跳步派发理算（无任何前置结论）→ 守卫改投 liability_judge，审计留痕。"""
    rec = MiniRecorder()

    async def illegal_router(state):
        return RoutingDecision(next=["amount_calc"], reason="LLM 想直接算钱")

    node = make_orchestrator_node(rec, illegal_router)
    update = await node({"case_id": "C1", "routing_calls": 1})

    assert update["pending_dispatch"] == ["liability_judge"]
    routing = next(e for e in rec.events if e["kind"] == "routing")
    assert routing["payload"]["mode"] == "llm"
    assert routing["payload"]["requested"] == ["amount_calc"]
    assert routing["payload"]["targets"] == ["liability_judge"]
    corrections = [e for e in rec.events if e["kind"] == "guard_correction"]
    assert len(corrections) == 1
    assert corrections[0]["payload"]["requested"] == ["amount_calc"]


async def test_llm_failure_falls_back_to_deterministic() -> None:
    """路由器抛错 → 回退 default_route（空案件 → 材料审核），mode 留痕。"""
    rec = MiniRecorder()

    async def boom(state):
        raise RuntimeError("LLM 供应商 500")

    node = make_orchestrator_node(rec, boom)
    update = await node({"case_id": "C1"})

    assert update["pending_dispatch"] == ["material_review"]
    routing = next(e for e in rec.events if e["kind"] == "routing")
    assert routing["payload"]["mode"] == "deterministic_fallback"


async def test_over_budget_skips_llm_and_forces_fallback() -> None:
    """超过调度调用预算 → 不再调用 LLM，强制确定性兜底（D039 防绕圈）。"""
    rec = MiniRecorder()
    calls: list[int] = []

    async def router(state):
        calls.append(1)
        return RoutingDecision(next=["material_review"], reason="x")

    node = make_orchestrator_node(rec, router)
    update = await node({"case_id": "C1", "routing_calls": 999})

    assert calls == []  # 超预算直接不调 LLM
    assert update["pending_dispatch"] == ["material_review"]
    routing = next(e for e in rec.events if e["kind"] == "routing")
    assert routing["payload"]["over_budget"] is True
    assert routing["payload"]["mode"] == "deterministic_fallback"


async def test_llm_human_decision_sets_request() -> None:
    """LLM 裁量转人工 → human_request 载荷（review），不派发 worker。"""
    rec = MiniRecorder()

    async def router(state):
        return RoutingDecision(next=["human"], reason="规则未覆盖的例外情形")

    node = make_orchestrator_node(rec, router)
    update = await node({"case_id": "C1", "routing_calls": 1})

    assert update["pending_dispatch"] is None
    assert update["human_request"]["kind"] == "review"
    assert update["human_request"]["reason"] == "规则未覆盖的例外情形"


async def test_llm_human_with_partial_material_becomes_supplement() -> None:
    """LLM 对缺件案裁量转人工 → kind 由代码判定为 supplement（补件闭环，非复核）。"""
    rec = MiniRecorder()

    async def router(state):
        return RoutingDecision(next=["human"], reason="材料不全")

    node = make_orchestrator_node(rec, router)
    update = await node(
        {
            "case_id": "C1",
            "routing_calls": 1,
            "material": {"completeness": "partial", "missing": ["费用清单"], "confidence": 1.0},
        }
    )
    assert update["human_request"]["kind"] == "supplement"
    assert update["human_request"]["missing"] == ["费用清单"]
    assert update["pending_dispatch"] is None
