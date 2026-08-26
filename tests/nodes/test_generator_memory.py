"""generator 节点的长期记忆注入测试（T035）。

验证 ReactAgentNode（system prefix）与 synthesize_answer_node（模板 memory 段）
两条回答出口路径在 state.memory_context 非空时注入历史记忆、为空时 prompt 不变。
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import nodes.generator as generator_module
from nodes.generator import ReactAgentNode, synthesize_answer_node
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry

_MEMORY_TEXT = "- 用户咨询保单 POL-2025-0001 阑尾炎理赔，预估赔付 4640 元"


class SpyModel:
    """记录收到的消息并返回固定回答（无 tool_calls → 直接终结）。"""

    def __init__(self) -> None:
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: Any, config: Any = None) -> AIMessage:
        self.calls.append(list(messages))
        return AIMessage(content="基于历史记忆的回答。")

    def bind_tools(self, specs: list[Any]) -> SpyModel:
        return self


@pytest.fixture()
def spy_model(monkeypatch: pytest.MonkeyPatch) -> SpyModel:
    spy = SpyModel()
    monkeypatch.setattr(generator_module, "get_chat_model", lambda *a, **k: spy)
    return spy


def _react_node() -> ReactAgentNode:
    return ReactAgentNode(executor=ToolExecutor(ToolRegistry()))


async def test_react_injects_memory_into_system_prompt(spy_model: SpyModel) -> None:
    """react 路径：memory_context 非空 → SystemMessage 附加历史记忆段。"""
    node = _react_node()
    state: dict[str, Any] = {
        "messages": [HumanMessage(content="我上次问的那张保单能赔多少")],
        "memory_context": _MEMORY_TEXT,
    }
    await node(state)
    first = spy_model.calls[0][0]
    assert isinstance(first, SystemMessage)
    assert "历史会话记忆" in first.content
    assert "POL-2025-0001" in first.content
    # 用户消息仍在（记忆只注入 system，不污染对话历史）
    assert any(isinstance(m, HumanMessage) for m in spy_model.calls[0])


async def test_react_empty_memory_unchanged_prompt(spy_model: SpyModel) -> None:
    """react 路径：memory_context 为空 → system prompt 与无记忆时完全一致。"""
    node = _react_node()
    state: dict[str, Any] = {"messages": [HumanMessage(content="你好")], "memory_context": ""}
    await node(state)
    first = spy_model.calls[0][0]
    assert isinstance(first, SystemMessage)
    assert "历史会话记忆" not in first.content


async def test_synthesize_injects_memory_into_prompt(spy_model: SpyModel) -> None:
    """synthesize 路径：memory_context 非空 → prompt 含记忆文本。"""
    state: dict[str, Any] = {
        "shared_data": {"medical": {"summary": "阑尾炎属保障范围"}},
        "messages": [HumanMessage(content="我上次问的那张保单能赔多少")],
        "memory_context": _MEMORY_TEXT,
    }
    result = await synthesize_answer_node(state)
    assert result["final_answer"]
    prompt = str(spy_model.calls[0][0].content)
    assert "POL-2025-0001" in prompt
    assert "历史会话记忆" in prompt


async def test_synthesize_without_memory_keeps_semantics(spy_model: SpyModel) -> None:
    """synthesize 路径：无 memory_context → 模板 memory 段传"无"（原语义保持）。"""
    state: dict[str, Any] = {
        "shared_data": {},
        "messages": [HumanMessage(content="你好")],
    }
    await synthesize_answer_node(state)
    prompt = str(spy_model.calls[0][0].content)
    assert "：\n无" in prompt
    assert "POL-2025-0001" not in prompt


async def test_step_executor_appends_memory_to_instruction(monkeypatch: pytest.MonkeyPatch) -> None:
    """multi_step 路径：memory_context 非空 → Worker 指令附加记忆段；为空不附加。"""
    import nodes.step_executor as step_executor_module
    from nodes.step_executor import StepExecutorNode

    received: list[str] = []

    async def fake_run(agent_def, instruction, shared_data, executor, tool_trace=None):  # noqa: ANN001
        received.append(instruction)
        return {"summary": "结论"}

    monkeypatch.setattr(step_executor_module, "run_worker_agent", fake_run)

    node = StepExecutorNode(executor=ToolExecutor(ToolRegistry()))
    base_state: dict[str, Any] = {
        "task_plan": [{"agent": "claim", "description": "核算赔付"}],
        "current_step": 0,
        "shared_data": {},
        "agent_steps": [],
        "tool_trace": [],
    }
    await node({**base_state, "memory_context": _MEMORY_TEXT})
    await node(
        {
            **base_state,
            "memory_context": "",
            "current_step": 0,
            "task_plan": [{"agent": "claim", "description": "核算赔付"}],
        }
    )

    assert "POL-2025-0001" in received[0]
    assert received[0].startswith("核算赔付")
    assert received[1] == "核算赔付"  # 空记忆时指令原样
