"""LLM Orchestrator 工作流级测试（T081）：并行派发并发断言 + 失败兜底 e2e。

脚本化路由器（不调真实 LLM）驱动 LLM 代码路径；真实 LLM 路由一致率初测见
scripts/verify_orchestrator.py（验收报告 evals/reports/t081_orchestrator_routing.json）。
"""

from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from nodes.orchestrator import RoutingDecision, default_route, make_llm_router
from workflows.case_graph import build_case_graph

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "mock"


class ListRecorder:
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


def _scripted_router():
    """脚本化路由器：把确定性兜底计划翻译成 RoutingDecision（走 LLM 代码路径）。"""

    async def router(state):
        targets, human = default_route(state)
        if human:
            return RoutingDecision(next=["human"], reason="scripted")
        return RoutingDecision(next=targets, reason="scripted")

    return router


def _graph(recorder: ListRecorder, router) -> Any:
    async def policy_lookup(policy_no: str) -> dict[str, Any] | None:
        return {
            "policy_no": policy_no,
            "product_type": "医疗险",
            "status": "active",
            "coverage_amount": "1000000.00",
            "deductible": "10000.00",
            "payout_ratio": "0.8000",
            "effective_date": "2025-01-01",
            "expiry_date": "2026-12-31",
        }

    async def fraud_lookup(user_id: str) -> dict[str, Any] | None:
        return None

    return build_case_graph(
        recorder=recorder,
        policy_lookup=policy_lookup,
        fraud_lookup=fraud_lookup,
        checkpointer=InMemorySaver(),
        orchestrator_router=router,
    )


def _input() -> dict[str, Any]:
    return {
        "case_id": "CASE-T081",
        "user_id": "u",
        "policy_id": "POL-2025-0001",
        "claimed_amount": Decimal("15800.00"),
        "incident_date": "2026-08-10",
        "incident_description": "急性阑尾炎住院手术，共花费15800元。",
        "materials": [
            {"file_name": "invoice.jpg", "doc_type": "invoice"},
            {"file_name": "diagnosis.jpg", "doc_type": "diagnosis"},
            {"file_name": "cost_list.pdf", "doc_type": "cost_list"},
        ],
    }


async def test_parallel_dispatch_is_concurrent() -> None:
    """Send 并行断言：保单/风控查询各睡 0.5s，两段执行区间必须重叠（真并行）。"""
    recorder = ListRecorder()
    timeline: list[tuple[str, float, float]] = []

    def slow_lookup(name: str, delay: float):
        async def _inner(*a, **k):
            start = time.perf_counter()
            await asyncio.sleep(delay)
            end = time.perf_counter()
            timeline.append((name, start, end))
            if name == "policy":
                return {
                    "policy_no": "POL-2025-0001",
                    "product_type": "医疗险",
                    "status": "active",
                    "coverage_amount": "1000000.00",
                    "deductible": "10000.00",
                    "payout_ratio": "0.8000",
                    "effective_date": "2025-01-01",
                    "expiry_date": "2026-12-31",
                }
            return None

        return _inner

    graph = build_case_graph(
        recorder=recorder,
        policy_lookup=slow_lookup("policy", 0.5),
        fraud_lookup=slow_lookup("fraud", 0.5),
        checkpointer=InMemorySaver(),
        orchestrator_router=_scripted_router(),
    )
    result = await graph.ainvoke(
        _input(), config={"configurable": {"thread_id": "CASE-T081-P"}}
    )
    assert result["final_decision"] == "approved"
    assert Decimal(str(result["approved_amount"])) == Decimal("4640.00")

    # policy 被 intake 与 policy_verify 各调一次；取第二次（核验）区间与风控区间
    policy_calls = [(s, e) for n, s, e in timeline if n == "policy"]
    fraud_calls = [(s, e) for n, s, e in timeline if n == "fraud"]
    assert len(policy_calls) >= 2 and len(fraud_calls) == 1
    p_start, p_end = policy_calls[-1]
    f_start, f_end = fraud_calls[0]
    overlap = max(p_start, f_start) < min(p_end, f_end)
    assert overlap, (
        f"保单核验与风控筛查必须并行执行（区间应重叠）：policy=[{p_start:.2f},{p_end:.2f}] "
        f"fraud=[{f_start:.2f},{f_end:.2f}]"
    )
    stages = [e["stage"] for e in recorder.events if e["kind"] == "stage_result"]
    assert "policy_verify" in stages and "fraud_check" in stages


async def test_router_failure_falls_back_e2e() -> None:
    """LLM 路由器全程故障 → 每轮回退确定性兜底，案件仍完整走完并正确签发。"""
    recorder = ListRecorder()

    async def broken_router(state):
        raise RuntimeError("供应商不可用")

    graph = _graph(recorder, broken_router)
    result = await graph.ainvoke(
        _input(), config={"configurable": {"thread_id": "CASE-T081-F"}}
    )
    assert result["final_decision"] == "approved"
    assert Decimal(str(result["approved_amount"])) == Decimal("4640.00")
    routing_events = [e for e in recorder.events if e["kind"] == "routing"]
    assert routing_events, "应有路由审计"
    assert all(
        e["payload"]["mode"] == "deterministic_fallback" for e in routing_events
    )
    # 预算内收敛
    assert all(e["payload"]["calls"] <= 15 for e in routing_events)


def test_make_llm_router_disabled_returns_none(monkeypatch) -> None:
    """配置关闭 → 工厂返回 None（纯确定性编排）。

    补丁打在 nodes.orchestrator 实际引用的 settings 对象上（仓库已知"settings
    双实例陷阱"，见 tests/conftest.py 与 progress T028/T029）。
    """
    import nodes.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module.settings, "orchestrator_llm_enabled", False)
    assert make_llm_router() is None


def test_skill_file_loaded_into_router_prompt(monkeypatch) -> None:
    """调度 skill 文件存在且可被装载（orchestrator/_shared.md 随 T081 落库）。"""
    from services.skills import load_skill

    skill = load_skill("orchestrator", "_shared")
    assert skill and "decision_generate" in skill
    # cases.json 金样本随任务落库（路由一致性评测的数据源）
    cases = json.loads((DATA_DIR / "cases.json").read_text(encoding="utf-8"))["cases"]
    assert len(cases) >= 20
