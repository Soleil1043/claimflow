"""任务规划与步骤执行节点测试（T017，F08）。

两层：
- Planner：LLM 规划（正常/markdown 包裹/非法 Agent 过滤/空步骤/异常兜底）、
  关键词规则兜底、节点封装
- StepExecutor：逐步执行（shared_data 写入 / agent_steps 轨迹 / 状态回写）、
  未知 Agent 与执行异常降级、条件边

真实 LLM 端到端验收（"阑尾炎手术能赔多少"≥2 步计划）见 scripts/verify_planner.py。
"""

from __future__ import annotations

from typing import Any

import pytest

import nodes.planner as planner_module
import nodes.step_executor as executor_module
from nodes.planner import _fallback_plan, create_plan, planner_node
from nodes.step_executor import StepExecutorNode, has_next_step
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry


class FakeModel:
    """可控 LLM：返回预设响应或抛异常。"""

    def __init__(self, response: Any = None, raise_exc: Exception | None = None) -> None:
        self._response = response
        self._raise = raise_exc

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        if self._raise:
            raise self._raise

        class _Resp:
            def __init__(self, content: str) -> None:
                self.content = content

        return _Resp(self._response)


def _patch_model(monkeypatch: pytest.MonkeyPatch, model: FakeModel) -> None:
    monkeypatch.setattr(planner_module, "get_chat_model", lambda *a, **k: model)


# ---------- Planner：关键词规则兜底 ----------


def test_fallback_plan_amount_and_medical() -> None:
    """金额+医疗 → medical→claim 两步（验收用例的兜底路径）。"""
    plan = _fallback_plan("我做了阑尾炎手术能赔多少")
    assert [s.agent for s in plan.steps] == ["medical", "claim"]
    assert all(s.step_index == i for i, s in enumerate(plan.steps))


def test_fallback_plan_amount_only() -> None:
    plan = _fallback_plan("保单能赔多少钱")
    assert [s.agent for s in plan.steps] == ["claim"]


def test_fallback_plan_medical_only() -> None:
    plan = _fallback_plan("我的阑尾炎诊断在保障范围吗")
    assert [s.agent for s in plan.steps] == ["medical"]


def test_fallback_plan_no_keyword() -> None:
    """未命中规则：单步 claim 兜底，描述携带原始诉求。"""
    plan = _fallback_plan("随便看看")
    assert [s.agent for s in plan.steps] == ["claim"]
    assert plan.steps[0].description == "随便看看"


# ---------- Planner：create_plan（mock LLM） ----------


async def test_create_plan_llm_success(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = """{"intent": "multi_step", "steps": [
        {"agent": "medical", "description": "核对诊断与材料"},
        {"agent": "claim", "description": "计算预估赔付金额"}
    ]}"""
    _patch_model(monkeypatch, FakeModel(response=raw))
    plan = await create_plan("我做了阑尾炎手术能赔多少")
    assert [s.agent for s in plan.steps] == ["medical", "claim"]
    assert plan.steps[0].step_index == 0
    assert plan.steps[1].step_index == 1


async def test_create_plan_markdown_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = '```json\n{"steps": [{"agent": "claim", "description": "查保单算金额"}]}\n```'
    _patch_model(monkeypatch, FakeModel(response=raw))
    plan = await create_plan("帮我算赔多少")
    assert [s.agent for s in plan.steps] == ["claim"]


async def test_create_plan_filters_invalid_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    """非法 Agent 名的步骤被过滤，合法步骤保留并重排序号。"""
    raw = """{"steps": [
        {"agent": "orchestrator", "description": "调度"},
        {"agent": "medical", "description": "医疗审核"},
        {"agent": "unknown_agent", "description": "未知"}
    ]}"""
    _patch_model(monkeypatch, FakeModel(response=raw))
    plan = await create_plan("阑尾炎能赔吗")
    assert [s.agent for s in plan.steps] == ["medical"]
    assert plan.steps[0].step_index == 0


async def test_create_plan_empty_steps_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 输出空步骤：走关键词兜底。"""
    _patch_model(monkeypatch, FakeModel(response='{"steps": []}'))
    plan = await create_plan("我做了阑尾炎手术能赔多少")
    assert [s.agent for s in plan.steps] == ["medical", "claim"]


async def test_create_plan_unparsable_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_model(monkeypatch, FakeModel(response="抱歉，我无法规划"))
    plan = await create_plan("我做了阑尾炎手术能赔多少")
    assert [s.agent for s in plan.steps] == ["medical", "claim"]


async def test_create_plan_llm_exception_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 调用异常（超时等）：走规则兜底，不抛错。"""
    _patch_model(monkeypatch, FakeModel(raise_exc=RuntimeError("LLM 超时")))
    plan = await create_plan("我做了阑尾炎手术能赔多少")
    assert [s.agent for s in plan.steps] == ["medical", "claim"]


async def test_create_plan_empty_input() -> None:
    """空输入：直接走兜底（未命中关键词 → 单步 claim）。"""
    plan = await create_plan("   ")
    assert len(plan.steps) == 1


# ---------- Planner：LangGraph 节点封装 ----------


async def test_planner_node_writes_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """节点：读末尾用户输入 → 写 task_plan / current_step / shared_data。"""
    from langchain_core.messages import AIMessage, HumanMessage

    raw = """{"steps": [
        {"agent": "medical", "description": "医疗审核"},
        {"agent": "claim", "description": "理赔核算"}
    ]}"""
    _patch_model(monkeypatch, FakeModel(response=raw))
    state = {
        "messages": [
            HumanMessage(content="旧问题"),
            AIMessage(content="旧回答"),
            HumanMessage(content="我做了阑尾炎手术能赔多少"),
        ]
    }
    update = await planner_node(state)
    assert [s["agent"] for s in update["task_plan"]] == ["medical", "claim"]
    assert update["current_step"] == 0
    assert update["shared_data"] == {}


# ---------- StepExecutor：逐步执行 ----------


def _make_executor() -> ToolExecutor:
    return ToolExecutor(ToolRegistry())


def _plan_state() -> dict[str, Any]:
    return {
        "task_plan": [
            {"step_index": 0, "agent": "medical", "description": "医疗审核", "status": "pending"},
            {"step_index": 1, "agent": "claim", "description": "理赔核算", "status": "pending"},
        ],
        "current_step": 0,
        "shared_data": {},
    }


async def test_step_executor_runs_one_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """执行第一步：结果写入 shared_data、agent_steps 记录、游标推进、状态回写。"""
    medical_result = {"summary": "阑尾炎 K35 在保障范围内", "missing_materials": []}

    async def fake_run(agent_def, instruction, shared_data, executor, tool_trace=None):  # noqa: ANN001
        return dict(medical_result)

    monkeypatch.setattr(executor_module, "run_worker_agent", fake_run)
    node = StepExecutorNode(_make_executor())
    update = await node(_plan_state())

    assert update["current_step"] == 1
    assert update["shared_data"]["medical"] == medical_result
    assert update["task_plan"][0]["status"] == "done"
    assert update["task_plan"][0]["result"] == medical_result
    assert update["task_plan"][1]["status"] == "pending"  # 后续步骤不受影响
    assert len(update["agent_steps"]) == 1
    step_record = update["agent_steps"][0]
    assert step_record["agent"] == "medical"
    assert step_record["status"] == "done"
    assert step_record["summary"] == medical_result["summary"]
    assert step_record["duration_ms"] >= 0


async def test_step_executor_passes_shared_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """第二步执行时能拿到第一步的 shared_data（步骤间数据传递）。"""
    seen: dict[str, Any] = {}

    async def fake_run(agent_def, instruction, shared_data, executor, tool_trace=None):  # noqa: ANN001
        seen["shared"] = shared_data
        return {"summary": f"{agent_def.name} 完成"}

    monkeypatch.setattr(executor_module, "run_worker_agent", fake_run)
    node = StepExecutorNode(_make_executor())
    state = _plan_state()
    state["current_step"] = 1
    state["shared_data"] = {"medical": {"summary": "医疗审核结论"}}
    update = await node(state)

    assert seen["shared"] == {"medical": {"summary": "医疗审核结论"}}
    assert update["shared_data"]["claim"]["summary"] == "claim 完成"
    assert update["shared_data"]["medical"] == {"summary": "医疗审核结论"}  # 前步数据保留


async def test_step_executor_collects_tool_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_worker_agent 内的工具调用就地追加进 tool_trace（F08 追溯）。"""

    async def fake_run(agent_def, instruction, shared_data, executor, tool_trace=None):  # noqa: ANN001
        if tool_trace is not None:
            tool_trace.append(
                {"agent": agent_def.name, "tool": "record_query", "input": {}, "output": {}}
            )
        return {"summary": "ok"}

    monkeypatch.setattr(executor_module, "run_worker_agent", fake_run)
    node = StepExecutorNode(_make_executor())
    update = await node(_plan_state())
    assert update["tool_trace"] == [
        {"agent": "medical", "tool": "record_query", "input": {}, "output": {}}
    ]


async def test_step_executor_unknown_agent_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """计划中出现未知 Agent：该步标记 failed，不抛错，游标照常推进。"""
    state = _plan_state()
    state["task_plan"][0]["agent"] = "nonexistent_agent"
    node = StepExecutorNode(_make_executor())
    update = await node(state)

    assert update["current_step"] == 1
    assert update["task_plan"][0]["status"] == "failed"
    assert update["agent_steps"][0]["status"] == "failed"


async def test_step_executor_exception_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_worker_agent 抛异常（LLM 故障等）：该步 failed，整体不阻断。"""

    async def fake_run(agent_def, instruction, shared_data, executor, tool_trace=None):  # noqa: ANN001
        raise RuntimeError("LLM 超时")

    monkeypatch.setattr(executor_module, "run_worker_agent", fake_run)
    node = StepExecutorNode(_make_executor())
    update = await node(_plan_state())

    assert update["current_step"] == 1
    assert update["task_plan"][0]["status"] == "failed"
    assert "执行失败" in update["task_plan"][0]["result"]["summary"]


async def test_step_executor_empty_plan_noop() -> None:
    """空计划 / 越界游标：防御性返回空更新。"""
    node = StepExecutorNode(_make_executor())
    assert await node({"task_plan": [], "current_step": 0}) == {}
    assert await node({"task_plan": [{"agent": "claim"}], "current_step": 5}) == {}


# ---------- 条件边 ----------


def test_has_next_step() -> None:
    plan = [{"agent": "medical"}, {"agent": "claim"}]
    assert has_next_step({"task_plan": plan, "current_step": 0}) == "next"
    assert has_next_step({"task_plan": plan, "current_step": 1}) == "next"
    assert has_next_step({"task_plan": plan, "current_step": 2}) == "done"
    assert has_next_step({"task_plan": [], "current_step": 0}) == "done"
    assert has_next_step({}) == "done"
