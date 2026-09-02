"""回答生成节点（T047 重构）：react 子图包装 + 回答整合。

- react_node：单领域/闲聊/其他路径——官方 create_agent 通用助手子图
  （全量守卫工具 + tools_condition 内置循环），替代 v1 手写 ReactAgentNode；
  LLM 故障降级话术保留（T022：LLM 超时场景不 500）
- synthesize_answer_node：多步 / RAG 路径的整合器——汇总 shared_data
  （各 Worker 结论或知识库检索上下文）生成面向用户的最终回答
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage

from app.core.logging import get_logger
from services.llm.client import get_chat_model
from services.llm.prompts import ANSWER_SYNTHESIS_PROMPT, GENERAL_ASSISTANT_PROMPT
from services.observability.token_tracker import phase_ainvoke
from state import AgentState
from tools.factory import get_default_tool_map

log = get_logger(__name__)

# react 子图缓存（编译一次；工具全量来自工厂装配的守卫工具）
_react_agent: Any = None


def get_react_agent() -> Any:
    """通用助手子图（全量工具；system prompt 静态，跨会话记忆经输入消息注入）。"""
    global _react_agent
    if _react_agent is None:
        from langchain.agents import create_agent
        from langchain.agents.middleware import ModelCallLimitMiddleware

        _react_agent = create_agent(
            model=get_chat_model(),
            tools=list(get_default_tool_map().values()),
            system_prompt=GENERAL_ASSISTANT_PROMPT,
            # 工具循环硬截断（v1 MAX_TOOL_ROUNDS=8 语义，T048）
            middleware=[ModelCallLimitMiddleware(run_limit=9, exit_behavior="end")],
            name="react",
        )
        log.info("react_agent_built", tools=len(get_default_tool_map()))
    return _react_agent


def reset_react_agent() -> None:
    """清空 react 子图缓存（测试用）。"""
    global _react_agent
    _react_agent = None


# LLM 故障降级话术（T022：LLM 超时场景不 500）
_REACT_FALLBACK_ANSWER = "抱歉，服务暂时繁忙，请稍后再试或转人工服务。"


async def react_node(state: AgentState) -> dict[str, Any]:
    """通用助手节点：create_agent 子图跑完工具循环，产出面向用户的回答。

    - 输入消息 = 主图消息（跨会话记忆以追加 SystemMessage 注入，置于
      子图静态 system prompt 之后，T035 语义不变）
    - 只把新增消息返回主图（工具轨迹随 messages 并入，A06 派生 used_tools）
    - 子图异常 → 降级话术（v1 ReactAgentNode 语义），不再循环
    """
    agent = get_react_agent()
    input_messages = list(state.get("messages") or [])
    memory_context = state.get("memory_context") or ""
    if memory_context:
        input_messages = [
            SystemMessage(
                content="## 用户历史会话记忆（此前会话的长期记忆，"
                "用于理解用户的指代与省略问句，如「上次问的那张保单」）\n" + memory_context
            )
        ] + input_messages

    try:
        result = await agent.ainvoke({"messages": input_messages})
    except Exception as exc:  # noqa: BLE001 LLM 故障降级
        log.warning("react_llm_error", error=str(exc)[:200])
        fallback = AIMessage(content=_REACT_FALLBACK_ANSWER)
        return {"messages": [fallback], "final_answer": _REACT_FALLBACK_ANSWER}

    new_messages = list(result["messages"][len(input_messages) :])
    ai_with_content = [
        m for m in new_messages if isinstance(m, AIMessage) and str(m.content).strip()
    ]
    answer = str(ai_with_content[-1].content).strip() if ai_with_content else ""
    if not answer:
        answer = _REACT_FALLBACK_ANSWER
        new_messages = new_messages + [AIMessage(content=answer)]
    log.info("react_node_done", new_messages=len(new_messages), answer_len=len(answer))
    return {"messages": new_messages, "final_answer": answer}


# ===== 回答整合节点（T021，F08：多步 / RAG 路径的结果整合） =====

# 历史消息条数上限（防 Token 失控）
_SYNTH_HISTORY_LIMIT = 10


def _format_history(messages: list[AnyMessage]) -> str:
    """消息历史 → 文本（截断至最近 N 条；跳过工具回执与空内容）。"""
    conversational = [
        m
        for m in messages
        if not isinstance(m, ToolMessage) and isinstance(m, (HumanMessage, AIMessage))
    ]
    lines = []
    for m in conversational[-_SYNTH_HISTORY_LIMIT:]:
        role = "用户" if isinstance(m, HumanMessage) else "助手"
        content = str(m.content)[:500]
        if content.strip():
            lines.append(f"{role}：{content}")
    return "\n".join(lines)


def _fallback_answer(shared_data: dict[str, Any]) -> str:
    """LLM 失败时的确定性兜底：拼接各数据源的 summary。"""
    summaries = []
    for source, data in shared_data.items():
        if isinstance(data, dict) and data.get("summary"):
            summaries.append(f"- {source}：{data['summary']}")
    if summaries:
        return "根据已获取的信息：\n" + "\n".join(summaries) + "\n（最终以理赔审核结果为准。）"
    return "抱歉，我暂时无法处理该问题，请稍后再试或转人工服务。"


async def synthesize_answer_node(state: AgentState) -> dict[str, Any]:
    """整合节点：基于 shared_data（Worker 结论 / RAG 上下文）生成最终回答。

    LLM 失败时降级为各数据源 summary 的确定性拼接，节点不抛错。
    """
    shared_data = state.get("shared_data") or {}
    messages = state.get("messages") or []

    context = json.dumps(shared_data, ensure_ascii=False, default=str)
    if len(context) > 6000:
        context = context[:6000] + "…（截断）"

    try:
        model = get_chat_model()
        response = await phase_ainvoke(
            model,
            [
                HumanMessage(
                    content=ANSWER_SYNTHESIS_PROMPT.format(
                        context=context,
                        history=_format_history(messages),
                        # T035：新会话首轮注入的历史记忆（空时传"无"，prompt 保持原语义）
                        memory=state.get("memory_context") or "无",
                    )
                )
            ],
            phase="generator",
        )
        answer = (response.content or "").strip()
        if answer:
            log.info("answer_synthesized", length=len(answer), sources=list(shared_data))
            return {"final_answer": answer}
        log.warning("synthesize_empty_output")
    except Exception as exc:  # noqa: BLE001 LLM 故障 → 确定性兜底
        log.warning("synthesize_llm_error", error=str(exc)[:200])

    answer = _fallback_answer(shared_data)
    return {"final_answer": answer}
