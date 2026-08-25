"""任务规划节点（T017，F08）。

Orchestrator 规划器：将 multi_step 用户诉求拆解为有序步骤计划，
每步指定一个 Worker Agent（medical / claim）。

LLM 结构化输出（TASK_PLANNER_PROMPT）+ 关键词规则兜底：
LLM 输出异常（解析失败 / 非法 Agent 名 / 空步骤）时按规则给出保底计划，
保证节点永不报错（可靠性要求与 intent 节点一致）。

T021 组装主图时由 intent 分流接入（multi_step → planner）。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage

from agents import CLAIM_AGENT, MEDICAL_AGENT
from app.core.logging import get_logger
from schemas.agent import TaskPlan, TaskStep
from services.llm.client import get_chat_model
from services.llm.prompts import TASK_PLANNER_PROMPT
from services.observability.llm_metrics import observed_ainvoke

log = get_logger(__name__)

VALID_PLAN_AGENTS = {"medical", "claim"}

# 关键词兜底规则（LLM 失败时的保底计划）
_AMOUNT_KEYWORDS = ["赔多少", "能赔", "报销多少", "赔付金额", "能报销", "多少钱"]
_MEDICAL_KEYWORDS = ["手术", "住院", "诊断", "病历", "就诊", "看病", "病", "医药费", "医药"]


def _fallback_plan(user_input: str) -> TaskPlan:
    """关键词规则兜底：金额+医疗 → medical→claim；仅金额 → claim；仅医疗 → medical。"""
    wants_amount = any(k in user_input for k in _AMOUNT_KEYWORDS)
    wants_medical = any(k in user_input for k in _MEDICAL_KEYWORDS)

    if wants_amount and wants_medical:
        steps = [
            TaskStep(step_index=0, agent="medical", description="查询就诊记录并核对诊断是否在保障范围内、是否有等待期或材料缺失"),
            TaskStep(step_index=1, agent="claim", description="查询相关保单信息，结合医疗审核结论与费用计算预估赔付金额"),
        ]
    elif wants_amount:
        steps = [TaskStep(step_index=0, agent="claim", description="查询保单信息并计算预估赔付金额")]
    elif wants_medical:
        steps = [TaskStep(step_index=0, agent="medical", description="查询就诊记录并核对诊断保障范围与材料")]
    else:
        # 未命中任何规则：单步 claim 兜底（中性，claim 工具集覆盖保单/RAG 查询）
        steps = [TaskStep(step_index=0, agent="claim", description=user_input[:200])]
    return TaskPlan(steps=steps)


def _parse_llm_json(raw: str) -> dict[str, Any] | None:
    """解析 LLM 输出的 JSON（容忍 markdown 代码块包裹/前后缀文本）。"""
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


async def create_plan(user_input: str) -> TaskPlan:
    """任务规划主入口：LLM 结构化输出 + 规则兜底。

    任何异常路径都不抛出，最坏返回兜底计划（节点可靠性要求）。
    """
    if not user_input.strip():
        return _fallback_plan(user_input)

    try:
        model = get_chat_model(temperature=0.0)
        prompt = TASK_PLANNER_PROMPT.format(
            medical_description=MEDICAL_AGENT.description,
            claim_description=CLAIM_AGENT.description,
            user_input=user_input,
        )
        response = await observed_ainvoke(model, [HumanMessage(content=prompt)])
        parsed = _parse_llm_json(response.content or "")

        if parsed and isinstance(parsed.get("steps"), list):
            # 过滤非法 Agent 名的步骤，重排 step_index
            valid_steps = [
                TaskStep(agent=str(s.get("agent", "")), description=str(s.get("description", "")))
                for s in parsed["steps"]
                if isinstance(s, dict) and s.get("agent") in VALID_PLAN_AGENTS and s.get("description")
            ]
            if valid_steps:
                for i, step in enumerate(valid_steps):
                    step.step_index = i
                plan = TaskPlan(steps=valid_steps)
                log.info(
                    "plan_created",
                    steps=len(valid_steps),
                    agents=[s.agent for s in valid_steps],
                    fallback=False,
                )
                return plan
        log.warning("planner_llm_invalid_output", raw=(response.content or "")[:100])
    except Exception as exc:
        log.warning("planner_llm_error", error=str(exc)[:200])

    plan = _fallback_plan(user_input)
    log.info("plan_created", steps=len(plan.steps), fallback=True)
    return plan


async def planner_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph 节点封装（T021 接入主图）。

    读 messages 末尾用户输入 → 生成计划 →
    写 task_plan（步骤 dict 列表）/ current_step=0 / shared_data={}。
    """
    messages = state.get("messages") or []
    last_human = next(
        (m.content for m in reversed(messages) if isinstance(m, HumanMessage)), ""
    )
    plan = await create_plan(str(last_human))
    return {
        "task_plan": [s.model_dump() for s in plan.steps],
        "current_step": 0,
        "shared_data": {},
    }
