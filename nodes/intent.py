"""意图识别节点（T013，F03）。

Few-shot Prompt + JSON 结构化输出（architecture.md 5.3 Intent Node），
关键词规则兜底：LLM 输出异常（非法标签/解析失败）时按规则给出保底分类，
保证节点永不报错（未知输入走兜底追问由下游 ReAct 节点的澄清行为承接）。

Phase 1：节点独立可用 + 测试集验证；T021 组装主图时作为入口节点接入分流。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage

from app.core.logging import get_logger
from schemas.agent import IntentResult
from services.llm.client import get_chat_model
from services.llm.prompts import INTENT_CLASSIFICATION_PROMPT

log = get_logger(__name__)

VALID_INTENTS = {"simple_faq", "single_domain", "multi_step", "chitchat", "other"}

# 关键词兜底规则（按优先级）：LLM 失败时的保底分类
_KEYWORD_RULES: list[tuple[str, str]] = [
    ("能赔多少", "multi_step"),
    ("赔多少", "multi_step"),
    ("能报销多少", "multi_step"),
    ("计算", "multi_step"),
    ("顺便", "multi_step"),
    ("保单", "single_domain"),
    ("进度", "single_domain"),
    ("身份证", "single_domain"),
    ("查一下", "single_domain"),
    ("你好", "chitchat"),
    ("谢谢", "chitchat"),
    ("天气", "chitchat"),
    ("你是", "chitchat"),
]


def _fallback_intent(user_input: str) -> str:
    """关键词规则兜底：无命中时归 simple_faq（中性，走 RAG 追问路径）。"""
    for keyword, intent in _KEYWORD_RULES:
        if keyword in user_input:
            return intent
    return "simple_faq"


def _parse_llm_json(raw: str) -> dict[str, Any] | None:
    """解析 LLM 输出的 JSON（容忍 markdown 代码块包裹）。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").lstrip()
        # 去掉可能的 ```json 前缀残留
        if text.startswith("json\n"):
            text = text[5:]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # 尝试提取第一个 {...} 片段
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


async def classify_intent(user_input: str) -> IntentResult:
    """意图分类主入口：LLM 结构化输出 + 规则兜底。

    任何异常路径都不抛出（节点可靠性要求），最坏返回兜底分类。
    """
    if not user_input.strip():
        return IntentResult(intent="chitchat", reason="空输入", fallback=False)

    try:
        model = get_chat_model(temperature=0.0)
        prompt = INTENT_CLASSIFICATION_PROMPT.format(user_input=user_input)
        response = await model.ainvoke([HumanMessage(content=prompt)])
        parsed = _parse_llm_json(response.content or "")
        if parsed and parsed.get("intent") in VALID_INTENTS:
            intent = str(parsed["intent"])
            reason = str(parsed.get("reason", ""))[:100]
            log.info("intent_classified", intent=intent, fallback=False)
            return IntentResult(intent=intent, reason=reason, fallback=False)
        # LLM 输出非法：走兜底
        log.warning("intent_llm_invalid_output", raw=(response.content or "")[:100])
    except Exception as exc:
        log.warning("intent_llm_error", error=str(exc)[:200])

    intent = _fallback_intent(user_input)
    log.info("intent_classified", intent=intent, fallback=True)
    return IntentResult(intent=intent, reason="关键词规则兜底", fallback=True)


async def intent_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph 节点封装（T021 接入主图）：读 messages 末尾用户输入，写 intent。"""
    from langchain_core.messages import HumanMessage

    messages = state.get("messages") or []
    last_human = next(
        (m.content for m in reversed(messages) if isinstance(m, HumanMessage)), ""
    )
    result = await classify_intent(str(last_human))
    return {"intent": result.intent}
