"""Worker Agent 执行器（T046，F08）。

以 AgentDefinition 为蓝本，用官方 `langchain.agents.create_agent` 装配 Worker 子图
（替代 v1 手写 ReAct 循环，D021/D022）：

- system_prompt（静态）/ tools（从默认工具图解析的守卫工具）/ response_format（结构化终局输出
  → result["structured_response"]，ToolStrategy：模型终局调用以 schema 命名的隐藏工具）
- 动态任务指令 + shared_data 上下文经输入 messages 注入（调用侧构造）
- tool_trace 由子图返回的 messages 派生（AIMessage.tool_calls ↔ ToolMessage 配对；
  排除结构化输出工具），A06 used_tools 口径不变
- 降级语义（v1 对齐）：子图异常向上抛（step_executor 捕获记 failed）；
  模型未产出结构化结论 → 最后一条 AIMessage 原文降级为 {"summary": ...}
- 防失控：recursion_limit 承载 v1 MAX_TOOL_ROUNDS（每轮 = 模型 + 工具两节点）
"""

from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from agents.base import AgentDefinition
from app.core.logging import get_logger
from services.llm.client import get_chat_model
from services.observability.token_tracker import (
    record_usage_to_tracker,
    track_phase,
)
from tools.factory import get_default_tool_map

log = get_logger(__name__)


class _WorkerTokenHandler(BaseCallbackHandler):
    """归集 Worker 子图内 LLM 调用的 token 用量（T029 轮次预算口径不变）。

    create_agent 内部自行调用模型，无法再经 phase_ainvoke 包装；
    官方扩展点为 callback：on_llm_end 读 usage_metadata 记入轮次 tracker。
    """

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        try:
            message = response.generations[0][0].message
            usage = getattr(message, "usage_metadata", None) or {}
            model = (response.llm_output or {}).get("model_name") or "unknown"
            if usage:
                record_usage_to_tracker(
                    model,
                    int(usage.get("input_tokens", 0)),
                    int(usage.get("output_tokens", 0)),
                )
        except (AttributeError, IndexError, TypeError, ValueError):
            pass  # 非标准响应（测试假件等）：记账跳过，不影响执行

# 单个 Worker 步骤内的工具循环预算（v1 MAX_TOOL_ROUNDS=8）。
# 硬截断由官方 ModelCallLimitMiddleware 承载（run_limit=9 ≈ 8 轮工具循环 + 终局），
# 超限 exit_behavior="end" 强制收口（与 v1 轮数到顶语义一致，T048 回归实测：
# "对比两张保单"类多查询任务模型会连续调工具不收口，仅靠 recursion_limit 会爆异常）。
MAX_TOOL_ROUNDS = 8
_RUN_MODEL_CALL_LIMIT = 9
_RECURSION_LIMIT = 50

# Worker 子图缓存（编译一次，进程内复用；测试可预置/重置）
_worker_cache: dict[str, Any] = {}


def get_worker_subgraph(agent_def: AgentDefinition) -> Any:
    """装配（带缓存）一个 Worker 的 create_agent 子图。"""
    if agent_def.name not in _worker_cache:
        tools = agent_def.resolve_tool_objects(get_default_tool_map())
        _worker_cache[agent_def.name] = create_agent(
            model=get_chat_model(),
            tools=tools,
            system_prompt=agent_def.system_prompt,
            response_format=agent_def.output_schema,
            middleware=[ModelCallLimitMiddleware(run_limit=_RUN_MODEL_CALL_LIMIT, exit_behavior="end")],
            name=agent_def.name,
        )
        log.info("worker_subgraph_built", agent=agent_def.name, tools=[t.name for t in tools])
    return _worker_cache[agent_def.name]


def reset_worker_cache() -> None:
    """清空 Worker 子图缓存（测试用：切换模型配置后重建）。"""
    _worker_cache.clear()


def _build_task_message(instruction: str, shared_data: dict[str, Any]) -> HumanMessage:
    """构造任务指令：用户诉求 + 前序步骤产出（共享数据池）。"""
    parts = [f"任务：{instruction}"]
    if shared_data:
        context = json.dumps(shared_data, ensure_ascii=False, default=str)
        # 截断超长上下文（防 Token 失控，shared_data 只保留关键结论）
        if len(context) > 3000:
            context = context[:3000] + "…（截断）"
        parts.append(f"\n前序步骤已获取的数据（可直接引用，勿重复查询）：\n{context}")
    parts.append("\n请按输出格式要求给出 JSON 结论。")
    return HumanMessage(content="\n".join(parts))


def _derive_tool_trace(
    agent_name: str, messages: list[Any], known_tools: set[str]
) -> list[dict[str, Any]]:
    """从 Worker 子图 messages 派生工具轨迹（按本 Agent 工具白名单过滤）。"""
    known = known_tools
    calls: dict[str, dict[str, Any]] = {}
    for m in messages:
        for tc in getattr(m, "tool_calls", None) or []:
            calls[tc["id"]] = tc

    trace: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, ToolMessage) or m.name not in known:
            continue
        tc = calls.get(m.tool_call_id, {})
        try:
            output = json.loads(m.content) if isinstance(m.content, str) else m.content
        except (json.JSONDecodeError, TypeError):
            output = {"raw": str(m.content)[:500]}
        trace.append(
            {"agent": agent_name, "tool": m.name, "input": tc.get("args", {}), "output": output}
        )
    return trace


def derive_tool_trace(
    messages: list[Any], exclude: set[str] | None = None
) -> list[dict[str, Any]]:
    """任意消息列表 → 工具轨迹（A06 used_tools / 评测口径）。

    AIMessage.tool_calls（id→name/args，AIMessage.name 为产出 Agent）与
    ToolMessage（tool_call_id→output）跨消息配对；exclude 用于剔除
    response_format 的隐藏结构化输出工具（以 schema 类名命名的非业务工具）。
    """
    exclude = exclude or set()
    calls: dict[str, tuple[str, dict[str, Any], str]] = {}
    trace: list[dict[str, Any]] = []
    for m in messages:
        owner = getattr(m, "name", None) or ""
        for tc in getattr(m, "tool_calls", None) or []:
            calls[tc["id"]] = (tc["name"], tc.get("args", {}) or {}, owner)
        if not isinstance(m, ToolMessage):
            continue
        name, args, owner = calls.get(m.tool_call_id, (getattr(m, "name", "") or "", {}, owner))
        if not name or name in exclude:
            continue
        try:
            output = json.loads(m.content) if isinstance(m.content, str) else m.content
        except (json.JSONDecodeError, TypeError):
            output = {"raw": str(m.content)[:500]}
        trace.append({"agent": owner, "tool": name, "input": args, "output": output})
    return trace


async def invoke_worker(
    agent_def: AgentDefinition, instruction: str, shared_data: dict[str, Any]
) -> tuple[dict[str, Any], list[Any]]:
    """执行一个 Worker 子图，返回（结构化结论 dict, 新增消息列表）。

    新增消息 = 子图 messages 中除注入任务消息外的全部（工具调用轨迹随之
    可被上层节点并入主图 messages，A06/评测从 messages 派生 used_tools）。
    结构化失败降级 {"summary": 原文}（不抛错）；子图执行异常向上抛。
    """
    worker = get_worker_subgraph(agent_def)
    input_messages = [_build_task_message(instruction, shared_data)]

    try:
        with track_phase("executor"):
            result = await worker.ainvoke(
                {"messages": input_messages},
                config={
                    "recursion_limit": _RECURSION_LIMIT,
                    "callbacks": [_WorkerTokenHandler()],
                },
            )
    except GraphRecursionError:
        # 防御性兜底（正常由 ModelCallLimitMiddleware 硬截断收口）：
        # 循环超限 → 按已获信息收口，不炸上层（v1 轮数到顶语义）
        log.warning("worker_recursion_limit_hit", agent=agent_def.name)
        return {"summary": "（已获取部分信息，未能完成全部查询，请基于现有结论回答或建议用户补充材料。）"}, []

    new_messages = list(result["messages"][len(input_messages) :])

    structured = result.get("structured_response")
    if structured is not None:
        log.info("worker_agent_done", agent=agent_def.name, structured=True)
        return structured.model_dump(), new_messages

    # 降级：模型未调用结构化输出工具 → 最后一条有内容的 AIMessage 原文（v1 语义）
    ai_messages = [m for m in new_messages if isinstance(m, AIMessage) and m.content]
    raw = str(ai_messages[-1].content)[:500] if ai_messages else ""
    log.warning("worker_output_unstructured", agent=agent_def.name, raw=raw[:100])
    return {"summary": raw}, new_messages


async def run_worker_agent(
    agent_def: AgentDefinition,
    instruction: str,
    shared_data: dict[str, Any],
    tool_trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """执行一个 Worker Agent 任务，返回结构化结论 dict（兼容入口，T046 测试口径）。

    Args:
        tool_trace: 可选轨迹列表（就地追加）：本步骤内每次工具调用记录
            {agent, tool, input, output}，供 A06 used_tools / F08 执行追溯。
    """
    result, new_messages = await invoke_worker(agent_def, instruction, shared_data)
    if tool_trace is not None:
        known = set(agent_def.tool_names)
        tool_trace.extend(_derive_tool_trace(agent_def.name, new_messages, known))
    return result
