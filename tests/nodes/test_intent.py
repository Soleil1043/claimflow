"""意图识别节点测试（F03；T045 结构化输出原生化 + D023 更名）。

两层：
- mock LLM：结构化输出（正常 / 调用异常 / schema 校验失败）、关键词兜底、空输入
- 真实 LLM 准确率验收（>=18/20）单独放 tests/llm/test_intent_accuracy.py，标记 slow

with_structured_output 的 mock 方式：FakeModel.with_structured_output(schema) 返回
可控 Runnable（ainvoke 返回 IntentClassification 实例或抛错），与 function calling
承载方式的异常路径（校验失败 / 调用失败）一一对应。
"""

from __future__ import annotations

from typing import Any

import pytest

import nodes.intent as intent_module
from nodes.intent import _fallback_intent, classify_intent, intent_node
from schemas.agent import IntentClassification, IntentType


class FakeStructured:
    """可控结构化输出 Runnable：返回预设 IntentClassification 或抛异常。"""

    def __init__(self, result: IntentClassification | None = None, raise_exc: Exception | None = None) -> None:
        self._result = result
        self._raise = raise_exc

    async def ainvoke(self, messages: Any, **kwargs: Any) -> IntentClassification:
        if self._raise:
            raise self._raise
        assert self._result is not None
        return self._result


class FakeModel:
    """可控 LLM：with_structured_output 返回预设结构化 Runnable。"""

    def __init__(self, result: IntentClassification | None = None, raise_exc: Exception | None = None) -> None:
        self._structured = FakeStructured(result, raise_exc)

    def with_structured_output(self, schema: Any, method: str | None = None) -> FakeStructured:
        assert method == "function_calling"
        return self._structured


def _patch_model(monkeypatch: pytest.MonkeyPatch, model: FakeModel) -> None:
    monkeypatch.setattr(intent_module, "get_chat_model", lambda *a, **k: model)


# ---------- 关键词兜底 ----------


def test_fallback_keywords() -> None:
    assert _fallback_intent("阑尾炎能赔多少") == "complex_consult"
    assert _fallback_intent("查一下我的保单") == "single_domain"
    assert _fallback_intent("你好呀") == "chitchat"
    assert _fallback_intent("随便说点什么") == "simple_faq"  # 默认


# ---------- classify_intent（mock LLM 结构化输出） ----------


async def test_classify_llm_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 结构化输出正常：无兜底，返回枚举值字符串。"""
    _patch_model(
        monkeypatch,
        FakeModel(result=IntentClassification(intent=IntentType.single_domain, reason="查保单")),
    )
    result = await classify_intent("帮我查保单 POL-2025-0001")
    assert result.intent == "single_domain"
    assert result.fallback is False


async def test_classify_llm_exception_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 调用异常（超时等）：走关键词兜底，节点不抛错。"""
    _patch_model(monkeypatch, FakeModel(raise_exc=RuntimeError("LLM 超时")))
    result = await classify_intent("阑尾炎能赔多少")
    assert result.fallback is True
    assert result.intent == "complex_consult"


async def test_classify_llm_validation_error_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """schema 校验失败（等价于旧'非法标签'路径）：走关键词兜底。"""
    _patch_model(monkeypatch, FakeModel(raise_exc=ValueError("校验失败：非法枚举")))
    result = await classify_intent("查一下保单")
    assert result.fallback is True
    assert result.intent == "single_domain"


async def test_classify_empty_input() -> None:
    """空输入：直接归 chitchat，不调 LLM。"""
    result = await classify_intent("   ")
    assert result.intent == "chitchat"
    assert result.fallback is False


# ---------- LangGraph 节点封装 ----------


async def test_intent_node_reads_last_human(monkeypatch: pytest.MonkeyPatch) -> None:
    """节点：读 messages 末尾 HumanMessage，写 intent 入 state。"""
    from langchain_core.messages import AIMessage, HumanMessage

    _patch_model(
        monkeypatch, FakeModel(result=IntentClassification(intent=IntentType.simple_faq, reason="知识"))
    )
    state = {
        "messages": [
            HumanMessage(content="之前的问题"),
            AIMessage(content="之前的回答"),
            HumanMessage(content="等待期是多久"),
        ]
    }
    update = await intent_node(state)
    assert update == {"intent": "simple_faq"}
