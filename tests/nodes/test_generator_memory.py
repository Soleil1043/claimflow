"""generator 节点的长期记忆注入测试（T035；T047 适配 react 子图包装节点）。

验证 react_node（记忆 SystemMessage 注入）与 synthesize_answer_node（模板 memory 段）
两条回答出口路径在 state.memory_context 非空时注入历史记忆、为空时不注入。
Worker 路径（指令附加记忆）见 tests/nodes/test_supervisor.py。
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import nodes.generator as generator_module
from nodes.generator import react_node, synthesize_answer_node

_MEMORY_TEXT = "- 用户咨询保单 POL-2025-0001 阑尾炎理赔，预估赔付 4640 元"


class SpyModel:
    """记录收到的消息并返回固定回答（无 tool_calls → 直接终结）。"""

    def __init__(self) -> None:
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: Any, config: Any = None) -> AIMessage:
        self.calls.append(list(messages))
        return AIMessage(content="基于历史记忆的回答。")

    def bind_tools(self, specs: list[Any], **kwargs: Any) -> SpyModel:
        return self

    def bind(self, **kwargs: Any) -> SpyModel:
        return self


@pytest.fixture()
def spy_model(monkeypatch: pytest.MonkeyPatch) -> SpyModel:
    spy = SpyModel()
    monkeypatch.setattr(generator_module, "get_chat_model", lambda *a, **k: spy)
    monkeypatch.setattr(generator_module, "_react_agent", None)  # 子图缓存重建拾取 spy
    yield spy
    monkeypatch.setattr(generator_module, "_react_agent", None)


def _memory_messages(call_messages: list[Any]) -> list[SystemMessage]:
    """模型首调中的记忆 SystemMessage（T047：注入于子图静态 system prompt 之后）。"""
    return [
        m
        for m in call_messages
        if isinstance(m, SystemMessage) and "历史会话记忆" in str(m.content)
    ]


async def test_react_injects_memory_system_message(spy_model: SpyModel) -> None:
    """react 路径：memory_context 非空 → 追加历史记忆 SystemMessage，用户消息保留。"""
    state: dict[str, Any] = {
        "messages": [HumanMessage(content="我上次问的那张保单能赔多少")],
        "memory_context": _MEMORY_TEXT,
    }
    result = await react_node(state)
    assert result["final_answer"] == "基于历史记忆的回答。"

    memory_msgs = _memory_messages(spy_model.calls[0])
    assert len(memory_msgs) == 1
    assert "POL-2025-0001" in memory_msgs[0].content
    assert any(isinstance(m, HumanMessage) for m in spy_model.calls[0])


async def test_react_empty_memory_no_injection(spy_model: SpyModel) -> None:
    """react 路径：memory_context 为空 → 无记忆消息注入。"""
    state: dict[str, Any] = {"messages": [HumanMessage(content="你好")], "memory_context": ""}
    await react_node(state)
    assert _memory_messages(spy_model.calls[0]) == []


async def test_react_llm_failure_fallback_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """react 路径：LLM 故障 → 降级话术（T022），不抛错。"""

    class _BrokenModel:
        def bind_tools(self, specs: Any, **kwargs: Any) -> _BrokenModel:
            return self

        async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
            raise RuntimeError("LLM 超时")

    monkeypatch.setattr(generator_module, "get_chat_model", lambda *a, **k: _BrokenModel())
    monkeypatch.setattr(generator_module, "_react_agent", None)

    state: dict[str, Any] = {"messages": [HumanMessage(content="查保单")], "memory_context": ""}
    result = await react_node(state)
    assert result["final_answer"] == "抱歉，服务暂时繁忙，请稍后再试或转人工服务。"
    monkeypatch.setattr(generator_module, "_react_agent", None)


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
