"""Supervisor 调度节点（T047，D021/ADR-007）：官方 multi-agent 动态路由。

替代 v1 planner（一次性计划）+ step_executor（游标循环）：

- 每轮结构化输出 RoutingDecision{next, plan, reason}（with_structured_output，
  function calling 承载），`Command(goto=...)` 路由到 Worker 子图节点或 FINISH→synthesize
- 首轮产出计划；后续轮读取 shared_data 已有结论推进状态，支持执行中重规划
  （v1 5.6 承诺未实现的"动态调整"在此落实）
- Worker 节点（make_worker_node）执行完回 supervisor（静态边），并把子图新增
  消息并入主图 messages（工具轨迹随 messages 派生，A06 used_tools 口径不变）
- 防失控：LLM 失败走关键词计划兜底（v1 planner 语义）；图级 recursion_limit 兜底
"""

from __future__ import annotations

import json
import time
from typing import Any, Literal

from langchain_core.messages import HumanMessage
from langgraph.types import Command
from pydantic import BaseModel

from agents import AgentDefinition, get_agent
from agents.runner import invoke_worker
from app.core.logging import get_logger
from services.llm.client import get_chat_model
from services.llm.prompts import SUPERVISOR_PROMPT
from services.observability.token_tracker import phase_ainvoke
from state import AgentState

log = get_logger(__name__)

# 调度可选目标：两个 Worker + FINISH（→ synthesize 节点）
SUPERVISOR_WORKERS = ("medical", "claim")


class PlanStepV2(BaseModel):
    """计划单步（supervisor 结构化输出用）。"""

    agent: Literal["medical", "claim"]
    description: str


class RoutingDecision(BaseModel):
    """supervisor 结构化输出：下一步路由 + 全量计划 + 理由。"""

    next: Literal["medical", "claim", "FINISH"]
    plan: list[PlanStepV2]
    reason: str = ""


def _fallback_plan(user_input: str) -> list[dict[str, Any]]:
    """关键词计划兜底（v1 planner 规则原样迁移）：金额+医疗 → medical→claim。"""
    amount_keywords = ["赔多少", "能赔", "报销多少", "赔付金额", "能报销", "多少钱"]
    medical_keywords = ["手术", "住院", "诊断", "病历", "就诊", "看病", "病", "医药费", "医药"]

    wants_amount = any(k in user_input for k in amount_keywords)
    wants_medical = any(k in user_input for k in medical_keywords)

    if wants_amount and wants_medical:
        steps = [
            {"agent": "medical", "description": "查询就诊记录并核对诊断是否在保障范围内、是否有等待期或材料缺失"},
            {"agent": "claim", "description": "查询相关保单信息，结合医疗审核结论与费用计算预估赔付金额"},
        ]
    elif wants_amount:
        steps = [{"agent": "claim", "description": "查询保单信息并计算预估赔付金额"}]
    elif wants_medical:
        steps = [{"agent": "medical", "description": "查询就诊记录并核对诊断保障范围与材料"}]
    else:
        steps = [{"agent": "claim", "description": user_input[:200]}]
    return steps


def _reconcile_plan(
    plan: list[dict[str, Any]], shared_data: dict[str, Any]
) -> list[dict[str, Any]]:
    """计划状态对账：已有结论的步骤置 done 并回填摘要，其余保持 pending。"""
    reconciled: list[dict[str, Any]] = []
    for step in plan:
        step = dict(step)
        agent = str(step.get("agent", ""))
        conclusion = shared_data.get(agent)
        if isinstance(conclusion, dict) and conclusion:
            step["status"] = "done"
            step.setdefault("summary", str(conclusion.get("summary", ""))[:200])
        else:
            step["status"] = "pending"
        reconciled.append(step)
    return reconciled


async def supervisor_node(state: AgentState) -> Command[Literal["medical", "claim", "synthesize"]]:
    """调度节点：结构化路由决策 + Command 动态路由（官方 supervisor 模式）。"""
    shared_data = dict(state.get("shared_data") or {})
    messages = state.get("messages") or []
    user_input = next((str(m.content) for m in reversed(messages) if isinstance(m, HumanMessage)), "")

    plan: list[dict[str, Any]] = []
    next_target: str = "FINISH"
    reason = ""
    try:
        model = get_chat_model(temperature=0.0)
        structured = model.with_structured_output(RoutingDecision, method="function_calling")
        # 计划进度上下文：已有结论（截断）+ 现有计划
        progress = {
            "conclusions": {k: v.get("summary", "") for k, v in shared_data.items() if isinstance(v, dict)},
            "current_plan": state.get("task_plan") or [],
        }
        decision: RoutingDecision = await phase_ainvoke(
            structured,
            [
                HumanMessage(
                    content=SUPERVISOR_PROMPT.format(
                        user_input=user_input[:2000],
                        progress=json.dumps(progress, ensure_ascii=False, default=str)[:3000],
                    )
                )
            ],
            phase="planner",
        )
        plan = [step.model_dump() for step in decision.plan]
        next_target = decision.next
        reason = decision.reason[:200]
    except Exception as exc:  # noqa: BLE001 LLM 故障 → 关键词计划兜底
        log.warning("supervisor_llm_error", error=str(exc)[:200])
        if not state.get("task_plan"):
            plan = _fallback_plan(user_input)
        else:
            plan = [
                {"agent": s.get("agent", "claim"), "description": s.get("description", "")}
                for s in (state.get("task_plan") or [])
            ]
        reason = "关键词计划兜底"
        # 兜底路径按计划顺序推进（路由守卫会按 pending 状态重定向/收敛）
        next_target = str(plan[0]["agent"]) if plan else "FINISH"

    # 状态对账：已有结论的步骤置 done；确定下一步（信任 LLM 决策，但目标必须有
    # 对应 pending 步骤，否则投首个 pending；无 pending → FINISH）
    plan = _reconcile_plan(plan, shared_data)
    pending_steps = [s for s in plan if s.get("status") == "pending"]
    if next_target != "FINISH" and next_target not in SUPERVISOR_WORKERS:
        next_target = "FINISH"
    if next_target != "FINISH":
        target_pending = any(
            s.get("agent") == next_target and s.get("status") == "pending" for s in plan
        )
        if not target_pending:
            next_target = str(pending_steps[0]["agent"]) if pending_steps else "FINISH"

    log.info(
        "supervisor_routed",
        next=next_target,
        steps=len(plan),
        done=sum(1 for s in plan if s.get("status") == "done"),
        reason=reason,
    )
    if next_target == "FINISH":
        return Command(goto="synthesize", update={"task_plan": plan})
    return Command(goto=next_target, update={"task_plan": plan})


def _pending_instruction(
    agent_def: AgentDefinition, state: AgentState
) -> tuple[dict[str, Any] | None, str]:
    """取该 Worker 的首个 pending 步骤及其指令（无则用末尾用户输入兜底）。"""
    from langchain_core.messages import HumanMessage as _HumanMessage

    for step in state.get("task_plan") or []:
        if step.get("agent") == agent_def.name and step.get("status") == "pending":
            return step, str(step.get("description", ""))
    messages = state.get("messages") or []
    user_input = next(
        (str(m.content) for m in reversed(messages) if isinstance(m, _HumanMessage)), ""
    )
    return None, user_input


def make_worker_node(agent_def: AgentDefinition):
    """Worker 图节点工厂：执行子图 → 结论入 shared_data、消息并入主图、计划状态推进。"""

    async def worker_node(state: AgentState) -> dict[str, Any]:
        step, instruction = _pending_instruction(agent_def, state)
        memory_context = state.get("memory_context") or ""
        if memory_context:
            # T035：跨会话记忆附加进步骤指令（仅 multi_step 路径需要，空记忆零影响）
            instruction += (
                "\n\n用户历史会话记忆（用于理解用户指代，如「上次问的那张保单」）：\n" + memory_context
            )

        shared_data = dict(state.get("shared_data") or {})
        started = time.perf_counter()
        result, new_messages = await invoke_worker(agent_def, instruction, shared_data)
        duration_ms = round((time.perf_counter() - started) * 1000)

        shared_data[agent_def.name] = result

        # 计划状态推进：首个 pending 步骤置 done（无显式步骤时不动计划）
        task_plan = [dict(s) for s in (state.get("task_plan") or [])]
        for s in task_plan:
            if s.get("agent") == agent_def.name and s.get("status") == "pending":
                s["status"] = "done"
                s["duration_ms"] = duration_ms
                s["summary"] = str(result.get("summary", ""))[:200]
                break

        log.info(
            "worker_node_done",
            agent=agent_def.name,
            duration_ms=duration_ms,
            new_messages=len(new_messages),
        )
        return {
            "shared_data": shared_data,
            "task_plan": task_plan,
            "messages": new_messages,  # 工具轨迹随 messages 并入主图（add_messages 合并）
        }

    return worker_node


def derive_agent_steps(
    task_plan: list[dict[str, Any]] | None, shared_data: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """task_plan + shared_data → agent_steps（A06 响应 / 审计口径，替代 v1 手写簿记）。"""
    steps: list[dict[str, Any]] = []
    for i, step in enumerate(task_plan or []):
        agent = str(step.get("agent", ""))
        conclusion = (shared_data or {}).get(agent)
        summary = str(step.get("summary", ""))
        if not summary and isinstance(conclusion, dict):
            summary = str(conclusion.get("summary", ""))
        steps.append(
            {
                "step_index": i,
                "agent": agent,
                "description": str(step.get("description", "")),
                "status": str(step.get("status", "pending")),
                "duration_ms": int(step.get("duration_ms", 0) or 0),
                "summary": summary[:200],
            }
        )
    return steps


def get_worker_agent(name: str) -> AgentDefinition:
    """按名取 Worker 定义（supervisor 路由目标校验用）。"""
    return get_agent(name)
