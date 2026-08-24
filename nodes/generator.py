"""ReAct Agent 节点（Phase 1 简版，F07 核心里程碑）+ 回答整合节点（T021，F08）。

- ReactAgentNode：单 Agent 循环：LLM + 工具绑定 → 有 tool_calls 则执行工具并回填 →
  无 tool_calls 则产出最终回答。由 LangGraph 条件边驱动循环（见 workflows/main_graph.py）。
- synthesize_answer_node：多步 / RAG 路径的整合器——汇总 shared_data
  （各 Worker Agent 结论或知识库检索上下文）生成面向用户的最终回答。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_core.runnables import ensure_config

from app.core.logging import get_logger
from services.llm.client import get_chat_model
from services.llm.prompts import ANSWER_SYNTHESIS_PROMPT, GENERAL_ASSISTANT_PROMPT
from state import AgentState
from tools.executor import ToolExecutor

log = get_logger(__name__)

# 防失控：单轮请求内最大工具调用轮数（含多工具并行调用）
MAX_TOOL_ROUNDS = 8


class ReactAgentNode:
    """Phase 1 单 Agent ReAct 节点（有状态：绑定执行器与工具集）。"""

    def __init__(self, executor: ToolExecutor, tool_names: list[str] | None = None) -> None:
        self._executor = executor
        registry = executor.registry
        self._tool_names = tool_names or registry.list_names()
        # OpenAI function calling 格式的工具定义（dict），
        # 而非项目 BaseTool 实例（langchain 无法识别自定义类）
        self._tool_specs = [registry.get(name).to_openai_tool() for name in self._tool_names]

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        """执行一轮：调 LLM，返回新增消息与轨迹。

        返回值约定（供条件边判断）：
        - LLM 请求工具 → 新增 AIMessage(tool_calls) + 本次执行的 ToolMessage(s)，
          conditions 检测 state 末尾是 ToolMessage 则继续循环
        - LLM 直接回答 → 新增 AIMessage(content)，final_answer 提取
        """
        model = get_chat_model()
        bound = model.bind_tools(self._tool_specs) if self._tool_specs else model

        messages: list[AnyMessage] = [
            *self._system_prefix(),
            *state["messages"],
        ]
        # 透传 LangGraph 运行上下文中的回调（tracing），缺省为 None 不影响执行
        config = ensure_config()
        response: AIMessage = await bound.ainvoke(messages, config=config)

        if not response.tool_calls:
            # 最终回答
            return {
                "messages": [response],
                "final_answer": response.content,
            }

        # 执行全部工具调用（顺序执行；轨迹记录入参/出参/耗时）
        trace: list[dict[str, Any]] = list(state.get("tool_trace") or [])
        tool_messages: list[ToolMessage] = []
        for call in response.tool_calls:
            result = await self._executor.execute(call["name"], call["args"])
            payload: dict[str, Any] = {
                "success": result.success,
                "error_message": result.error_message,
                **result.data,
            }
            tool_messages.append(
                ToolMessage(content=str(payload), tool_call_id=call["id"], name=call["name"])
            )
            trace.append({"tool": call["name"], "input": call["args"], "output": payload})
            log.info(
                "react_tool_executed",
                tool=call["name"],
                success=result.success,
            )

        return {
            "messages": [response, *tool_messages],
            "tool_trace": trace,
        }

    @staticmethod
    def _system_prefix() -> list[AnyMessage]:
        """系统提示（Phase 1 通用助手；T015 拆分 Agent 后替换为路由分发）。"""
        from langchain_core.messages import SystemMessage

        return [SystemMessage(content=GENERAL_ASSISTANT_PROMPT)]


def should_continue(state: AgentState) -> str:
    """条件边：末尾消息判断是否继续工具循环。

    返回 "tools"（继续）或 "end"（产出最终回答）。
    超过 MAX_TOOL_ROUNDS 强制结束（防失控）。
    """
    messages = state.get("messages") or []
    if messages and isinstance(messages[-1], ToolMessage):
        tool_rounds = len(state.get("tool_trace") or [])
        if tool_rounds >= MAX_TOOL_ROUNDS:
            log.warning("react_max_rounds_reached", rounds=tool_rounds)
            return "end"
        return "tools"
    return "end"


# ===== 回答整合节点（T021，F08：多步 / RAG 路径的结果整合） =====

# 历史消息条数上限（防 Token 失控）
_SYNTH_HISTORY_LIMIT = 10


def _format_history(messages: list[AnyMessage]) -> str:
    """消息历史 → 文本（截断至最近 N 条）。"""
    lines = []
    for m in messages[-_SYNTH_HISTORY_LIMIT:]:
        role = "用户" if isinstance(m, HumanMessage) else "助手"
        content = str(m.content)[:500]
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
    """整合节点：基于 shared_data（Agent 结论 / RAG 上下文）生成最终回答。

    LLM 失败时降级为各数据源 summary 的确定性拼接，节点不抛错。
    """
    shared_data = state.get("shared_data") or {}
    messages = state.get("messages") or []

    context = json.dumps(shared_data, ensure_ascii=False, default=str)
    if len(context) > 6000:
        context = context[:6000] + "…（截断）"

    try:
        model = get_chat_model()
        response = await model.ainvoke(
            [
                HumanMessage(
                    content=ANSWER_SYNTHESIS_PROMPT.format(
                        context=context, history=_format_history(messages)
                    )
                )
            ]
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
