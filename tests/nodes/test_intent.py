"""意图识别节点测试（F03）。

两层：
- mock LLM：JSON 解析（正常/markdown 包裹/残缺输出/非法标签）、关键词兜底、异常兜底、空输入
- 真实 LLM 准确率验收（>=18/20）单独放 tests/llm/test_intent_accuracy.py，标记 slow
"""

from __future__ import annotations

from typing import Any

import pytest

import nodes.intent as intent_module
from nodes.intent import _fallback_intent, _parse_llm_json, classify_intent, intent_node


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
    monkeypatch.setattr(intent_module, "get_chat_model", lambda *a, **k: model)


# ---------- JSON 解析 ----------


def test_parse_plain_json() -> None:
    assert _parse_llm_json('{"intent": "simple_faq", "reason": "知识咨询"}') == {
        "intent": "simple_faq",
        "reason": "知识咨询",
    }


def test_parse_markdown_wrapped_json() -> None:
    raw = '```json\n{"intent": "chitchat", "reason": "问候"}\n```'
    parsed = _parse_llm_json(raw)
    assert parsed is not None
    assert parsed["intent"] == "chitchat"


def test_parse_json_with_prefix_text() -> None:
    raw = '分类结果如下：{"intent": "multi_step", "reason": "查数据+算金额"} 请参考'
    parsed = _parse_llm_json(raw)
    assert parsed is not None
    assert parsed["intent"] == "multi_step"


def test_parse_invalid_returns_none() -> None:
    assert _parse_llm_json("完全不是 JSON") is None
    assert _parse_llm_json("") is None


# ---------- 关键词兜底 ----------


def test_fallback_keywords() -> None:
    assert _fallback_intent("阑尾炎能赔多少") == "multi_step"
    assert _fallback_intent("查一下我的保单") == "single_domain"
    assert _fallback_intent("你好呀") == "chitchat"
    assert _fallback_intent("随便说点什么") == "simple_faq"  # 默认


# ---------- classify_intent（mock LLM） ----------


async def test_classify_llm_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 正常返回：无兜底。"""
    _patch_model(monkeypatch, FakeModel(response='{"intent": "single_domain", "reason": "查保单"}'))
    result = await classify_intent("帮我查保单 POL-2025-0001")
    assert result.intent == "single_domain"
    assert result.fallback is False


async def test_classify_llm_invalid_label_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 返回非法标签：走关键词兜底。"""
    _patch_model(monkeypatch, FakeModel(response='{"intent": "unknown_label", "reason": "x"}'))
    result = await classify_intent("阑尾炎能赔多少")
    assert result.fallback is True
    assert result.intent == "multi_step"


async def test_classify_llm_unparsable_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 返回非 JSON：走关键词兜底。"""
    _patch_model(monkeypatch, FakeModel(response="抱歉我无法分类"))
    result = await classify_intent("查一下保单")
    assert result.fallback is True
    assert result.intent == "single_domain"


async def test_classify_llm_exception_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 调用异常（超时等）：走关键词兜底，节点不抛错。"""
    _patch_model(monkeypatch, FakeModel(raise_exc=RuntimeError("LLM 超时")))
    result = await classify_intent("阑尾炎能赔多少")
    assert result.fallback is True
    assert result.intent == "multi_step"


async def test_classify_empty_input() -> None:
    """空输入：直接归 chitchat，不调 LLM。"""
    result = await classify_intent("   ")
    assert result.intent == "chitchat"
    assert result.fallback is False


# ---------- LangGraph 节点封装 ----------


async def test_intent_node_reads_last_human(monkeypatch: pytest.MonkeyPatch) -> None:
    """节点：读 messages 末尾 HumanMessage，写 intent 入 state。"""
    from langchain_core.messages import AIMessage, HumanMessage

    _patch_model(monkeypatch, FakeModel(response='{"intent": "simple_faq", "reason": "知识"}'))
    state = {
        "messages": [
            HumanMessage(content="之前的问题"),
            AIMessage(content="之前的回答"),
            HumanMessage(content="等待期是多久"),
        ]
    }
    update = await intent_node(state)
    assert update == {"intent": "simple_faq"}
