"""步骤执行节点（T017，F08）。

按 task_plan 顺序逐步执行：每步按 agent 名找到 AgentDefinition，
经 run_worker_agent 完成 ReAct 工具循环，产出结构化结论：

- 结果写入 shared_data[agent_name]（供后续步骤与结果整合读取）
- 步骤状态回写 task_plan（pending → done / failed）
- agent_steps 记录每步执行档案（agent/描述/状态/耗时/结论摘要）
- tool_trace 就地追加该步内的全部工具调用（F08 执行追溯）

由条件边 has_next_step 驱动循环（step_executor → step_executor → … → done），
T021 组装主图时接入。
"""

from __future__ import annotations

import time
from typing import Any

from agents import get_agent
from agents.runner import run_worker_agent
from app.core.logging import get_logger
from state import AgentState
from tools.executor import ToolExecutor

log = get_logger(__name__)


class StepExecutorNode:
    """步骤执行节点（有状态：绑定工具执行器）。"""

    def __init__(self, executor: ToolExecutor) -> None:
        self._executor = executor

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        """执行 current_step 指向的一步，推进游标并回写全部状态。"""
        plan = list(state.get("task_plan") or [])
        idx = state.get("current_step", 0)
        if not plan or idx >= len(plan):
            return {}  # 无待执行步骤（条件边已保证不会走到，防御性返回）

        step = dict(plan[idx])
        agent_name = str(step.get("agent", ""))
        description = str(step.get("description", ""))

        shared = dict(state.get("shared_data") or {})
        agent_steps: list[dict[str, Any]] = list(state.get("agent_steps") or [])
        tool_trace: list[dict[str, Any]] = list(state.get("tool_trace") or [])

        started = time.perf_counter()
        result: dict[str, Any]
        status: str
        try:
            agent_def = get_agent(agent_name)
            # T035：历史会话记忆附加进步骤指令——Worker 能理解"上次问的那张保单"类
            # 跨会话指代（仅 multi_step 路径需要；空记忆时指令与原行为完全一致）
            instruction = description
            memory_context = state.get("memory_context") or ""
            if memory_context:
                instruction += (
                    "\n\n用户历史会话记忆（用于理解用户指代，如「上次问的那张保单」）：\n"
                    + memory_context
                )
            result = await run_worker_agent(
                agent_def, instruction, dict(shared), self._executor, tool_trace=tool_trace
            )
            status = "done"
            shared[agent_def.name] = result
        except KeyError:
            log.warning("step_unknown_agent", step=idx, agent=agent_name)
            result = {"summary": f"未知 Agent：{agent_name}"}
            status = "failed"
        except Exception as exc:  # noqa: BLE001 单步失败不阻断整体计划
            log.warning("step_execution_failed", step=idx, agent=agent_name, error=str(exc)[:200])
            result = {"summary": f"步骤执行失败：{exc}"[:300]}
            status = "failed"

        duration_ms = round((time.perf_counter() - started) * 1000)
        step["status"] = status
        step["result"] = result
        agent_steps.append(
            {
                "step_index": idx,
                "agent": agent_name,
                "description": description,
                "status": status,
                "duration_ms": duration_ms,
                "summary": str(result.get("summary", ""))[:200],
            }
        )
        log.info(
            "step_executed",
            step=idx,
            agent=agent_name,
            status=status,
            duration_ms=duration_ms,
        )

        new_plan = [*plan]
        new_plan[idx] = step
        return {
            "task_plan": new_plan,
            "current_step": idx + 1,
            "shared_data": shared,
            "agent_steps": agent_steps,
            "tool_trace": tool_trace,
        }


def has_next_step(state: AgentState) -> str:
    """条件边：还有未执行步骤 → "next"（回 step_executor）；否则 "done"。"""
    plan = state.get("task_plan") or []
    idx = state.get("current_step", 0)
    return "next" if idx < len(plan) else "done"
