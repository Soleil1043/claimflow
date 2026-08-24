"""F08 验收脚本：任务规划与步骤执行节点（真实 LLM 端到端）。

验收标准（tasks.md T017）：
- "我做了阑尾炎手术能赔多少"生成 ≥2 步计划（医疗审核→理赔核算）依次执行
- 每步结果写入 shared_data
- 执行记录可在响应追溯（agent_steps / tool_trace）

前置：.env 配置真实 LLM API Key（同 T012）；Mock 数据由脚本自行 seed（幂等）。
"""

from __future__ import annotations

import asyncio

import tools.claim  # noqa: F401 注册理赔工具
import tools.medical  # noqa: F401 注册医疗工具
from nodes.planner import create_plan
from nodes.step_executor import StepExecutorNode, has_next_step
from scripts.seed import seed_medical_records, seed_policies
from services.db.session import dispose_engine, init_db
from tools.executor import ToolExecutor
from tools.registry import get_default_registry

QUESTION = "我做了阑尾炎手术能赔多少"

# 防失控：步骤循环上限（正常 1-3 步）
MAX_ROUNDS = 10


async def main() -> None:
    # 数据准备（幂等）：dev profile aiosqlite
    await init_db()
    await seed_policies()
    await seed_medical_records()

    # 1. 任务规划（真实 LLM）
    plan = await create_plan(QUESTION)
    print(f"用户诉求：{QUESTION}")
    print(f"计划步骤（{len(plan.steps)} 步）：")
    for step in plan.steps:
        print(f"  [{step.step_index}] {step.agent:8s} {step.description}")

    assert len(plan.steps) >= 2, f"计划步骤数 {len(plan.steps)} < 2"
    assert plan.steps[0].agent == "medical", "第一步应为医疗审核"
    assert plan.steps[1].agent == "claim", "第二步应为理赔核算"

    # 2. 逐步执行（模拟主图循环：step_executor → 条件边 → step_executor → …）
    executor = ToolExecutor(get_default_registry())
    node = StepExecutorNode(executor)
    state: dict = {
        "task_plan": [s.model_dump() for s in plan.steps],
        "current_step": 0,
        "shared_data": {},
        "tool_trace": [],
        "agent_steps": [],
    }
    rounds = 0
    while has_next_step(state) == "next":
        update = await node(state)
        state.update(update)
        rounds += 1
        if rounds > MAX_ROUNDS:
            raise RuntimeError("步骤循环超出上限，疑似失控")

    # 3. 结果断言与追溯输出
    shared = state["shared_data"]
    assert "medical" in shared, "医疗审核结果未写入 shared_data"
    assert "claim" in shared, "理赔核算结果未写入 shared_data"

    print("\n===== shared_data（步骤间数据传递） =====")
    for agent, result in shared.items():
        print(f"  [{agent}] {str(result.get('summary', ''))[:120]}")

    print("\n===== agent_steps（执行档案） =====")
    for record in state["agent_steps"]:
        print(
            f"  [{record['step_index']}] {record['agent']:8s} {record['status']:6s}"
            f" {record['duration_ms']}ms | {record['summary'][:80]}"
        )

    print("\n===== tool_trace（工具调用追溯） =====")
    for trace in state["tool_trace"]:
        print(f"  [{trace['agent']:8s}] {trace['tool']}")

    assert len(state["agent_steps"]) == len(plan.steps), "执行档案与计划步骤数不一致"
    assert all(r["status"] == "done" for r in state["agent_steps"]), "存在失败步骤"
    assert state["tool_trace"], "工具调用轨迹为空（Worker 未调用任何工具）"

    await dispose_engine()
    print("\nF08 验收通过：≥2 步计划依次执行，结果入 shared_data，执行记录可追溯")


asyncio.run(main())
