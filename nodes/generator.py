"""ReAct Agent 节点（Phase 1 简版，F07 核心里程碑）。

单 Agent 循环：LLM + 工具绑定 → 有 tool_calls 则执行工具并回填 →
无 tool_calls 则产出最终回答。由 LangGraph 条件边驱动循环（见 workflows/main_graph.py）。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_core.runnables import ensure_config

from app.core.logging import get_logger
from services.llm.client import get_chat_model
from services.llm.prompts import GENERAL_ASSISTANT_PROMPT
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
