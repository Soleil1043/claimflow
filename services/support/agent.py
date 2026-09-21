"""客服 Agent 装配与会话执行（T133，D057）。

照 worker_agent 的 create_agent 模式（模型 / 工具 / 中间件三要素），两处差异：
- 对话型：无 response_format，自然语言终局；多轮记忆 = 会话表历史窗口 replay
  （D057-2，不接 checkpointer——LLM 无状态，replay 与 checkpoint 每轮 token
  成本等价，而转人工后坐席消息与用户消息留在同一时间线）
- 转人工为标记式：escalate_to_human 工具不改状态，真正的 ai → escalated 流转
  由 reply() 在 AI 终局话术落库后执行——转接话术写入 AI 时间线，不撞 T132
  状态机（escalated 后 assistant 停答）
"""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolErrorMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.errors import GraphRecursionError

from app.core.config import settings
from app.core.exceptions import SupportStateError
from app.core.logging import get_logger
from services.db.models import SupportMessage
from services.llm.client import get_chat_model
from services.llm.prompts import SUPPORT_AGENT_PROMPT
from services.support import store
from tools.factory import get_default_tool_map

log = get_logger(__name__)

SUPPORT_AGENT_NAME = "support_agent"
# 客服 Agent 专用工具集（核赔 worker 的险种 pack 不引用这四件）
SUPPORT_TOOL_NAMES = [
    "claim_rule_rag",
    "case_status_query",
    "claim_draft_link",
    "escalate_to_human",
]

# 与 worker 同预算：≈8 轮工具循环 + 终局
_RUN_MODEL_CALL_LIMIT = 9
_RECURSION_LIMIT = 50

# 子图缓存（编译一次，进程内复用；测试可重置）
_agent_cache: dict[str, Any] = {}


def _tool_error_message(exc: Exception, request: Any) -> str:
    """工具系统异常 → 模型可见的收敛消息（与 worker 同口径，只暴露异常类型）。"""
    name = (request.tool_call or {}).get("name", "unknown")
    return (
        f"工具 {name} 执行失败（{type(exc).__name__}）。"
        "请改用其他工具或调整入参重试；确实无法完成时，基于已获取的信息回复客户，"
        "或建议转人工。"
    )


def get_support_agent() -> Any:
    """装配（带缓存）客服 Agent 子图。"""
    if SUPPORT_AGENT_NAME not in _agent_cache:
        tool_map = get_default_tool_map()
        tools = [tool_map[name] for name in SUPPORT_TOOL_NAMES if name in tool_map]
        _agent_cache[SUPPORT_AGENT_NAME] = create_agent(
            model=get_chat_model(),
            tools=tools,
            system_prompt=SUPPORT_AGENT_PROMPT,
            middleware=[
                ToolErrorMiddleware(on_error=_tool_error_message),
                ModelCallLimitMiddleware(run_limit=_RUN_MODEL_CALL_LIMIT, exit_behavior="end"),
            ],
            name=SUPPORT_AGENT_NAME,
        )
        log.info("support_agent_built", tools=[t.name for t in tools])
    return _agent_cache[SUPPORT_AGENT_NAME]


def reset_support_agent_cache() -> None:
    """清空客服 Agent 子图缓存（测试用）。"""
    _agent_cache.clear()


def _replay_messages(history: list[SupportMessage]) -> list[BaseMessage]:
    """会话历史 → LLM 输入消息（user→Human / assistant→AI，保持时序）。"""
    replay: list[BaseMessage] = []
    for m in history:
        if m.role == store.ROLE_USER:
            replay.append(HumanMessage(content=m.content))
        elif m.role == store.ROLE_ASSISTANT:
            replay.append(AIMessage(content=m.content))
    return replay


def _final_ai_text(messages: list[BaseMessage]) -> str:
    """子图消息 → 终局回复文本（最后一条有内容的 AIMessage）。"""
    ai_texts = [str(m.content) for m in messages if isinstance(m, AIMessage) and m.content]
    return ai_texts[-1] if ai_texts else ""


def _escalation_reason(messages: list[BaseMessage]) -> str | None:
    """终局轨迹中是否触发了转人工（工具标记式判定，确定性代码）。

    返回转人工原因（工具入参 reason），未触发返回 None。
    """
    for m in messages:
        for tc in getattr(m, "tool_calls", None) or []:
            if tc.get("name") == "escalate_to_human":
                reason = (tc.get("args") or {}).get("reason")
                return str(reason) if reason else "AI 判定需人工介入"
    return None


_EMPTY_FALLBACK = "抱歉，我暂时无法回答这个问题。您可以换个问法，或选择转人工客服。"
_RECURSION_FALLBACK = "抱歉，处理您的请求时遇到困难，请稍后重试，或选择转人工客服。"


async def reply(conversation_id: str, user_message: str) -> str:
    """执行一个对话轮：落用户消息 → replay 历史 → Agent → 落 AI 终局消息。

    转人工：终局轨迹含 escalate_to_human 调用时，在 AI 话术落库后执行
    ai → escalated 流转（reason 取工具入参）。

    Raises:
        LookupError: 会话不存在。
        SupportStateError: 会话非 ai 态（escalated 由坐席接管 / closed 终态），
            调用方（API 层）应拒答而非调用本函数。
    """
    conv = await store.get_conversation(conversation_id)
    if conv is None:
        raise LookupError(f"conversation not found: {conversation_id}")
    if conv.status != store.CONV_AI:
        raise SupportStateError(
            f"conversation {conversation_id} is {conv.status}, AI reply disabled"
        )

    await store.append_message(
        conversation_id, role=store.ROLE_USER, content=user_message
    )
    history = await store.recent_messages(
        conversation_id, limit=settings.support_history_window
    )
    agent = get_support_agent()
    new_messages: list[BaseMessage] = []
    try:
        result = await agent.ainvoke(
            {"messages": _replay_messages(history)},
            config={"recursion_limit": _RECURSION_LIMIT},
        )
        new_messages = list(result["messages"])
    except GraphRecursionError:
        log.warning("support_agent_recursion_limit_hit", conversation_id=conversation_id)
        final_text = _RECURSION_FALLBACK
    else:
        final_text = _final_ai_text(new_messages) or _EMPTY_FALLBACK

    await store.append_message(
        conversation_id, role=store.ROLE_ASSISTANT, content=final_text
    )

    reason = _escalation_reason(new_messages)
    if reason is not None:
        try:
            await store.escalate_conversation(conversation_id, reason=reason)
        except (LookupError, SupportStateError) as exc:
            # 并发流转冲突（如用户侧同时关闭）——话术已落库，只告警
            log.warning(
                "support_escalate_conflict", conversation_id=conversation_id, error=str(exc)
            )
    return final_text
