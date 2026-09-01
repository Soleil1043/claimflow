"""Supervisor 调度节点测试（T047）：路由决策 / 计划对账 / Worker 节点 / 步骤推导。

覆盖：
- 关键词计划兜底（v1 planner 规则迁移）
- 计划对账（shared_data 已有结论 → done + 摘要回填）
- supervisor_node：LLM 结构化路由 → Command(goto)；LLM 故障 → 兜底计划；
  静态决策的收敛守卫（目标无 pending → 改投/结束）
- Worker 节点工厂：结论入 shared_data、计划状态推进、消息并入、记忆注入
- derive_agent_steps：A06/审计口径
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import HumanMessage

import nodes.supervisor as supervisor_module
from agents import CLAIM_AGENT, MEDICAL_AGENT
from nodes.supervisor import (
    _fallback_plan,
    _reconcile_plan,
    derive_agent_steps,
    make_worker_node,
    supervisor_node,
)


def _patch_llm(monkeypatch: pytest.MonkeyPatch, decision: dict[str, Any]) -> None:
    """结构化路由 LLM 打桩：预设 RoutingDecision JSON。"""
    import json as json_mod

    class FakeModel:
        def with_structured_output(self, schema: Any, method: str | None = None) -> Any:
            assert method == "function_calling"

            class _Structured:
                def __init__(self, content: str, schema: Any) -> None:
                    self._content = content
                    self._schema = schema

                async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
                    return self._schema.model_validate(json_mod.loads(self._content))

            return _Structured(json_mod.dumps(decision, ensure_ascii=False), schema)

    monkeypatch.setattr(supervisor_module, "get_chat_model", lambda *a, **k: FakeModel())


def _state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "messages": [],
        "intent": "complex_consult",
        "task_plan": [],
        "shared_data": {},
    }
    base.update(overrides)
    return base


# ---------- 关键词计划兜底 ----------


def test_fallback_plan_amount_and_medical() -> None:
    plan = _fallback_plan("我做了阑尾炎手术能赔多少")
    assert [s["agent"] for s in plan] == ["medical", "claim"]


def test_fallback_plan_claim_only() -> None:
    plan = _fallback_plan("帮我算一下报销多少")
    assert [s["agent"] for s in plan] == ["claim"]


# ---------- 计划对账 ----------


def test_reconcile_marks_done_and_keeps_pending() -> None:
    plan = [
        {"agent": "medical", "description": "核对诊断"},
        {"agent": "claim", "description": "核算金额"},
    ]
    reconciled = _reconcile_plan(plan, {"medical": {"summary": "K35 在保障范围内"}})
    assert reconciled[0]["status"] == "done"
    assert "K35" in reconciled[0]["summary"]
    assert reconciled[1]["status"] == "pending"


# ---------- supervisor_node ----------


async def test_supervisor_routes_first_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """首轮：LLM 决策 medical + 2 步计划 → Command(goto=medical)，计划入 state。"""
    _patch_llm(
        monkeypatch,
        {
            "next": "medical",
            "plan": [
                {"agent": "medical", "description": "核对诊断"},
                {"agent": "claim", "description": "核算金额"},
            ],
        },
    )
    command = await supervisor_node(_state(messages=[HumanMessage("我做了手术能赔多少")]))
    assert command.goto == "medical"
    assert [s["agent"] for s in command.update["task_plan"]] == ["medical", "claim"]
    assert command.update["task_plan"][0]["status"] == "pending"


async def test_supervisor_finishes_when_all_done(monkeypatch: pytest.MonkeyPatch) -> None:
    """全部步骤已有结论：即使静态决策仍指向 worker，也收敛到 FINISH。"""
    _patch_llm(
        monkeypatch,
        {
            "next": "medical",
            "plan": [{"agent": "medical", "description": "核对诊断"}],
        },
    )
    command = await supervisor_node(
        _state(shared_data={"medical": {"summary": "已完成"}})
    )
    assert command.goto == "synthesize"
    assert command.update["task_plan"][0]["status"] == "done"


async def test_supervisor_redirects_static_decision_to_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """静态决策指向已完成目标：自动改投首个 pending（多轮收敛守卫）。"""
    _patch_llm(
        monkeypatch,
        {
            "next": "medical",
            "plan": [
                {"agent": "medical", "description": "核对诊断"},
                {"agent": "claim", "description": "核算金额"},
            ],
        },
    )
    command = await supervisor_node(
        _state(shared_data={"medical": {"summary": "已完成"}})
    )
    assert command.goto == "claim"
    assert command.update["task_plan"][0]["status"] == "done"


async def test_supervisor_llm_failure_falls_back_to_keyword_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 故障（首轮）：关键词计划兜底，不抛错。"""

    class _BrokenModel:
        def with_structured_output(self, schema: Any, method: str | None = None) -> Any:
            raise RuntimeError("LLM 超时")

    monkeypatch.setattr(supervisor_module, "get_chat_model", lambda *a, **k: _BrokenModel())

    command = await supervisor_node(
        _state(messages=[HumanMessage("我做了阑尾炎手术能赔多少")])
    )
    assert command.goto == "medical"
    assert [s["agent"] for s in command.update["task_plan"]] == ["medical", "claim"]


# ---------- Worker 节点工厂 ----------


async def test_worker_node_updates_shared_data_and_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker 节点：结论入 shared_data、pending 步骤置 done、消息并入主图。"""
    captured: dict[str, Any] = {}

    async def fake_invoke(agent_def, instruction, shared_data):  # noqa: ANN001
        captured["agent"] = agent_def.name
        captured["instruction"] = instruction
        captured["shared_data"] = dict(shared_data)
        return {"summary": "K35 在保障范围内"}, ["<worker-new-message>"]

    monkeypatch.setattr(supervisor_module, "invoke_worker", fake_invoke)

    node = make_worker_node(MEDICAL_AGENT)
    state = _state(
        messages=["<user-message>"],
        task_plan=[
            {"agent": "medical", "description": "核对诊断", "status": "pending"},
            {"agent": "claim", "description": "核算金额", "status": "pending"},
        ],
        memory_context="上次问过保单",
    )
    update = await node(state)

    assert captured["agent"] == "medical"
    assert captured["instruction"].startswith("核对诊断")
    assert "上次问过保单" in captured["instruction"]  # T035 记忆注入
    assert update["shared_data"]["medical"]["summary"] == "K35 在保障范围内"
    assert update["task_plan"][0]["status"] == "done"
    assert update["task_plan"][1]["status"] == "pending"  # 只推进本 Agent 的步骤
    assert update["task_plan"][0]["duration_ms"] >= 0
    assert update["messages"] == ["<worker-new-message>"]  # 子图消息并入主图


async def test_worker_node_without_pending_step_uses_user_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无显式计划步骤（重规划场景）：指令退化为末尾用户输入。"""
    captured: dict[str, Any] = {}

    async def fake_invoke(agent_def, instruction, shared_data):  # noqa: ANN001
        captured["instruction"] = instruction
        return {"summary": "ok"}, []

    monkeypatch.setattr(supervisor_module, "invoke_worker", fake_invoke)
    from langchain_core.messages import HumanMessage

    node = make_worker_node(CLAIM_AGENT)
    await node(_state(messages=[HumanMessage("帮我算赔付")]))
    assert captured["instruction"] == "帮我算赔付"


# ---------- derive_agent_steps ----------


def test_derive_agent_steps_shape() -> None:
    task_plan = [
        {
            "agent": "medical",
            "description": "核对诊断",
            "status": "done",
            "duration_ms": 120,
            "summary": "K35 在保障范围内",
        },
        {"agent": "claim", "description": "核算金额", "status": "pending"},
    ]
    steps = derive_agent_steps(task_plan, {"medical": {"summary": "K35"}})
    assert steps[0] == {
        "step_index": 0,
        "agent": "medical",
        "description": "核对诊断",
        "status": "done",
        "duration_ms": 120,
        "summary": "K35 在保障范围内",
    }
    # 无 step 级 summary 时回退 shared_data 结论摘要
    assert steps[1]["summary"] == ""
    assert steps[1]["status"] == "pending"


def test_derive_agent_steps_empty() -> None:
    assert derive_agent_steps(None, None) == []
