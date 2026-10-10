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


async def _begin_turn(conversation_id: str, user_message: str) -> list[BaseMessage]:
    """轮次前置（reply / reply_stream 共享）：会话校验 → 落用户消息 → replay 历史。

    Raises:
        LookupError: 会话不存在。
        SupportStateError: 会话非 ai 态（escalated 由坐席接管 / closed 终态）。
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
    return _replay_messages(history)


async def _finish_turn(
    conversation_id: str, new_messages: list[BaseMessage], final_text: str
) -> str:
    """轮次后置（共享）：落 AI 终局 → 转人工流转 → 回会话终态（ai | escalated）。"""
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
    conv = await store.get_conversation(conversation_id)
    return conv.status if conv else store.CONV_AI


async def reply(conversation_id: str, user_message: str) -> str:
    """执行一个对话轮（非流式）：落用户消息 → replay 历史 → Agent → 落 AI 终局消息。

    转人工：终局轨迹含 escalate_to_human 调用时，在 AI 话术落库后执行
    ai → escalated 流转（reason 取工具入参）。

    Raises:
        LookupError: 会话不存在。
        SupportStateError: 会话非 ai 态（escalated 由坐席接管 / closed 终态），
            调用方（API 层）应拒答而非调用本函数。
    """
    replay = await _begin_turn(conversation_id, user_message)
    agent = get_support_agent()
    new_messages: list[BaseMessage] = []
    try:
        result = await agent.ainvoke(
            {"messages": replay},
            config={"recursion_limit": _RECURSION_LIMIT},
        )
        new_messages = list(result["messages"])
    except GraphRecursionError:
        log.warning("support_agent_recursion_limit_hit", conversation_id=conversation_id)
        final_text = _RECURSION_FALLBACK
    else:
        final_text = _final_ai_text(new_messages) or _EMPTY_FALLBACK

    await _finish_turn(conversation_id, new_messages, final_text)
    return final_text


async def reply_stream(conversation_id: str, user_message: str):
    """流式对话轮（T180，D085）：与 reply() 同语义，AI 文本 token 逐段产出。

    异步生成器，产出 (kind, payload)：
    - ("token", str)：流式文本片段（含 ReAct 中间轮叙述——调用方 done 后整体
      刷新校准，服务端时间线单源，中间轮文本只作瞬态展示）
    - ("done", "ai"|"escalated")：终局（AI 话术已落库；escalated 即转人工已流转）
    - ("error", str)：LLM 故障——用户消息已落库、assistant 不落，下一条消息
      重试不丢时间线（与非流式 503 路径语义一致）

    Raises（首次迭代时）: LookupError / SupportStateError——API 层开流前已校验，
    此处兜底（经调用方转 error 事件）。
    """
    replay = await _begin_turn(conversation_id, user_message)
    # 本轮 AIMessage 全集（on_chat_model_end 逐次累积）：终局文本与转人工判定源
    ai_messages: list[BaseMessage] = []
    try:
        agent = get_support_agent()
        async for ev in agent.astream_events(
            {"messages": replay},
            version="v2",
            config={"recursion_limit": _RECURSION_LIMIT},
        ):
            if ev["event"] == "on_chat_model_stream":
                chunk = (ev.get("data") or {}).get("chunk")
                text = getattr(chunk, "content", "")
                if isinstance(text, str) and text:
                    yield ("token", text)
            elif ev["event"] == "on_chat_model_end":
                output = (ev.get("data") or {}).get("output")
                if isinstance(output, AIMessage):
                    ai_messages.append(output)
    except GraphRecursionError:
        log.warning("support_agent_recursion_limit_hit", conversation_id=conversation_id)
        yield ("token", _RECURSION_FALLBACK)
        final_text = _RECURSION_FALLBACK
    except Exception as exc:  # noqa: BLE001 LLM 故障：用户消息已落库，经事件传达
        log.warning(
            "support_stream_failed", conversation_id=conversation_id, error=str(exc)[:200]
        )
        yield ("error", "在线客服暂时不可用，请稍后重试")
        return
    else:
        final_text = _final_ai_text(ai_messages) or _EMPTY_FALLBACK

    status = await _finish_turn(conversation_id, ai_messages, final_text)
    yield ("done", status)
