"""Worker Agent 执行器（T017，F08）。

以 AgentDefinition 为蓝本执行一次完整 Worker 任务：
system prompt + 任务描述（含 shared_data 上下文）→ ReAct 工具循环 →
最终输出解析为该 Agent 的结构化 schema。

与 Phase 1 ReactAgentNode 的差异：
- prompt/工具集/输出 schema 来自 AgentDefinition（多 Agent 专业化）
- 输出是给 Orchestrator 整合的结构化 JSON（非面向用户的最终话术）
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage

from agents.base import AgentDefinition
from app.core.logging import get_logger
from schemas.tools import ToolOutput
from services.llm.client import get_chat_model
from services.observability.token_tracker import phase_ainvoke
from tools.executor import ToolExecutor

log = get_logger(__name__)

# 单个 Worker 步骤内的工具循环上限（与 Phase 1 一致）
MAX_TOOL_ROUNDS = 8


def _parse_agent_json(raw: str) -> dict[str, Any] | None:
    """解析 Worker 最终输出的 JSON（容忍 markdown 包裹/前后缀）。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _build_task_message(
    agent_def: AgentDefinition, instruction: str, shared_data: dict[str, Any]
) -> HumanMessage:
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


async def run_worker_agent(
    agent_def: AgentDefinition,
    instruction: str,
    shared_data: dict[str, Any],
    executor: ToolExecutor,
    tool_trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """执行一个 Worker Agent 任务，返回结构化结论 dict。

    Args:
        tool_trace: 可选轨迹列表（就地追加）：本步骤内每次工具调用记录
            {agent, tool, input, output}，供 A06 used_tools / F08 执行追溯。

    任何解析失败都不抛错：降级为 {"summary": 原始文本} 由上层整合。
    """
    model = get_chat_model()
    specs = agent_def.resolve_tools(executor.registry)
    bound = model.bind_tools(specs) if specs else model

    messages: list[AnyMessage] = [
        SystemMessage(content=agent_def.system_prompt),
        _build_task_message(agent_def, instruction, shared_data),
    ]

    tool_rounds = 0
    while True:
        response: AIMessage = await phase_ainvoke(bound, messages, phase="executor")
        messages.append(response)

        if not response.tool_calls or tool_rounds >= MAX_TOOL_ROUNDS:
            break

        # 执行本轮全部工具调用并回填
        for call in response.tool_calls:
            result: ToolOutput = await executor.execute(call["name"], call["args"])
            payload = {
                "success": result.success,
                "error_message": result.error_message,
                **result.data,
            }
            messages.append(
                ToolMessage(content=json.dumps(payload, ensure_ascii=False, default=str),
                            tool_call_id=call["id"], name=call["name"])
            )
            if tool_trace is not None:
                tool_trace.append(
                    {"agent": agent_def.name, "tool": call["name"], "input": call["args"], "output": payload}
                )
        tool_rounds += 1

    # 最终输出解析为 Agent 结构化 schema
    raw_output = response.content or ""
    parsed = _parse_agent_json(raw_output)
    if parsed is not None:
        try:
            validated = agent_def.output_schema.model_validate(parsed)
            result_dict = validated.model_dump()
        except Exception:  # noqa: BLE001 schema 不匹配时降级
            result_dict = parsed
    else:
        log.warning("worker_output_unparsed", agent=agent_def.name, raw=raw_output[:100])
        result_dict = {"summary": raw_output[:500]}

    log.info(
        "worker_agent_done",
        agent=agent_def.name,
        tool_rounds=tool_rounds,
        parsed=parsed is not None,
    )
    return result_dict
