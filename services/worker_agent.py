"""Worker Agent 装配与执行（T046 引入；T096 自 agents/ 迁入并去 v1 死重）。

以 AgentDefinition 为蓝本，用官方 `langchain.agents.create_agent` 装配 Worker 子图：

- system_prompt（静态）/ tools（从默认工具图解析的守卫工具）/ response_format（结构化终局输出
  → result["structured_response"]，ToolStrategy：模型终局调用以 schema 命名的隐藏工具）
- 任务指令经输入 messages 注入（调用侧构造）
- tool_trace 由子图返回的 messages 派生（AIMessage.tool_calls ↔ ToolMessage 配对；
  排除结构化输出工具）
- 降级语义：子图异常向上抛（调用方节点走确定性兜底，D039）；
  模型未产出结构化结论 → 最后一条 AIMessage 原文降级为 {"summary": ...}
- 防失控：ModelCallLimitMiddleware 硬截断（run_limit=9 ≈ 8 轮工具循环 + 终局）

v1 死重（run_worker_agent 兼容入口、_derive_tool_trace、shared_data 共享数据池）
已随 T096 删除——唯一消费者 liability_judge 以两参 invoker 调用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

from app.core.logging import get_logger
from services.llm.client import get_chat_model
from services.observability.token_tracker import (
    record_usage_to_tracker,
    track_phase,
)
from tools.factory import get_default_tool_map

log = get_logger(__name__)


@dataclass(frozen=True)
class AgentDefinition:
    """Agent 静态定义（create_agent 三要素：system_prompt / tools / response_format）。

    Attributes:
        name: Agent 标识（图节点 / 计划步骤引用）
        display_name: 展示名（日志 / 演示）
        system_prompt: 系统提示词（来自 services/llm/prompts.py 常量）
        tool_names: 可用工具名列表（执行时从工具工厂的默认工具图解析）
        output_schema: 结构化终局输出 schema（response_format，Pydantic 模型）
        description: Agent 职责一句话描述
    """

    name: str
    display_name: str
    system_prompt: str
    tool_names: list[str]
    output_schema: type[BaseModel]
    description: str
    # Agent 专属运行参数（预留）
    extra: dict[str, Any] = field(default_factory=dict)

    def resolve_tool_objects(self, tool_map: dict[str, BaseTool]) -> list[BaseTool]:
        """从工具图解析本 Agent 可用的工具对象（跳过未装配的）。"""
        return [tool_map[name] for name in self.tool_names if name in tool_map]


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
# 超限 exit_behavior="end" 强制收口（多查询任务模型会连续调工具不收口，仅靠
# recursion_limit 会爆异常——T048 回归实测）。
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


def _build_task_message(instruction: str) -> HumanMessage:
    """构造任务指令消息。"""
    return HumanMessage(content=f"任务：{instruction}\n\n请按输出格式要求给出 JSON 结论。")


def derive_tool_trace(
    messages: list[Any], exclude: set[str] | None = None
) -> list[dict[str, Any]]:
    """任意消息列表 → 工具轨迹（审计 / 评测口径）。

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
    agent_def: AgentDefinition, instruction: str
) -> tuple[dict[str, Any], list[Any]]:
    """执行一个 Worker 子图，返回（结构化结论 dict, 新增消息列表）。

    新增消息 = 子图 messages 中除注入任务消息外的全部（工具调用轨迹随之
    可被上层节点并入主图 messages）。
    结构化失败降级 {"summary": 原文}（不抛错）；子图执行异常向上抛。
    """
    worker = get_worker_subgraph(agent_def)
    input_messages = [_build_task_message(instruction)]

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
        # 循环超限 → 按已获信息收口，不炸上层
        log.warning("worker_recursion_limit_hit", agent=agent_def.name)
        return {"summary": "（已获取部分信息，未能完成全部查询，请基于现有结论回答或建议用户补充材料。）"}, []

    new_messages = list(result["messages"][len(input_messages) :])

    structured = result.get("structured_response")
    if structured is not None:
        log.info("worker_agent_done", agent=agent_def.name, structured=True)
        return structured.model_dump(), new_messages

    # 降级：模型未调用结构化输出工具 → 最后一条有内容的 AIMessage 原文
    ai_messages = [m for m in new_messages if isinstance(m, AIMessage) and m.content]
    raw = str(ai_messages[-1].content)[:500] if ai_messages else ""
    log.warning("worker_output_unstructured", agent=agent_def.name, raw=raw[:100])
    return {"summary": raw}, new_messages
