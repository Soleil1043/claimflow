"""意图识别节点（T013，F03；T045 结构化输出原生化 + D023 更名）。

官方 Routing 模式：with_structured_output（IntentType 枚举，function calling 承载）
+ 条件边分流（workflows/main_graph.py）。关键词规则兜底保留：LLM 异常
（调用失败 / schema 校验不过）时按规则给出保底分类，保证节点永不报错
（未知输入走兜底追问由下游 ReAct 节点的澄清行为承接）。

Phase 1：节点独立可用 + 测试集验证；T021 组装主图时作为入口节点接入分流。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage

from app.core.logging import get_logger
from schemas.agent import IntentClassification, IntentResult
from services.llm.client import get_chat_model
from services.llm.prompts import INTENT_CLASSIFICATION_PROMPT
from services.observability.token_tracker import phase_ainvoke

log = get_logger(__name__)

# 关键词兜底规则（按优先级）：LLM 失败时的保底分类
_KEYWORD_RULES: list[tuple[str, str]] = [
    ("能赔多少", "complex_consult"),
    ("赔多少", "complex_consult"),
    ("能报销多少", "complex_consult"),
    ("计算", "complex_consult"),
    ("顺便", "complex_consult"),
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


async def classify_intent(user_input: str) -> IntentResult:
    """意图分类主入口：LLM 结构化输出（枚举约束）+ 规则兜底。

    任何异常路径都不抛出（节点可靠性要求），最坏返回兜底分类。
    """
    if not user_input.strip():
        return IntentResult(intent="chitchat", reason="空输入", fallback=False)

    try:
        model = get_chat_model(temperature=0.0)
        structured = model.with_structured_output(IntentClassification, method="function_calling")
        prompt = INTENT_CLASSIFICATION_PROMPT.format(user_input=user_input)
        result: IntentClassification = await phase_ainvoke(
            structured, [HumanMessage(content=prompt)], phase="intent"
        )
        log.info("intent_classified", intent=result.intent.value, fallback=False)
        return IntentResult(intent=result.intent.value, reason=result.reason[:100], fallback=False)
    except Exception as exc:  # noqa: BLE001 LLM 故障 / 校验失败 → 关键词兜底
        log.warning("intent_llm_error", error=str(exc)[:200])

    intent = _fallback_intent(user_input)
    log.info("intent_classified", intent=intent, fallback=True)
    return IntentResult(intent=intent, reason="关键词规则兜底", fallback=True)


async def intent_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph 节点封装（T021 接入主图）：读 messages 末尾用户输入，写 intent。"""
    messages = state.get("messages") or []
    last_human = next(
        (m.content for m in reversed(messages) if isinstance(m, HumanMessage)), ""
    )
    result = await classify_intent(str(last_human))
    return {"intent": result.intent}
