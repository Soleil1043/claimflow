"""T021 验收脚本：完整主图端到端联调（真实 LLM）。

验收标准（tasks.md T021）：
- A06 返回完整结构（answer/intent/used_tools/agent_steps/compliance_status/need_human_intervention）
- 多步任务全链路跑通（intent → planner → step_executor×N → synthesize → compliance）
- 服务重启后历史会话可继续（F14：共享 checkpointer 重建图实例模拟重启；
  prod 部署下为 PostgreSQL 持久化，语义相同）

覆盖场景：
1. multi_step："我做了阑尾炎手术能赔多少"（医疗审核→理赔核算 两步）
2. simple_faq："阑尾炎手术有等待期吗"（RAG 检索路径）
3. chitchat："你好"（ReAct 直答路径）
4. F14 多轮上下文：同会话追问，第二轮引用第一轮结论

前置：.env 配置真实 LLM API Key；知识库已入库（uv run python -m services.rag.ingest）。
"""

from __future__ import annotations

import asyncio

import tools.claim  # noqa: F401 注册理赔工具
import tools.compliance  # noqa: F401 注册合规工具
import tools.medical  # noqa: F401 注册医疗工具
from langchain_core.messages import HumanMessage
from scripts.seed import seed_medical_records, seed_policies
from services.db.session import dispose_engine, init_db
from tools.executor import ToolExecutor
from tools.registry import get_default_registry
from workflows.main_graph import build_main_graph

# 每轮输入的全量重置字段（与 A06 保持一致）
RESET_INPUT = {
    "intent": None,
    "task_plan": [],
    "current_step": 0,
    "shared_data": {},
    "agent_steps": [],
    "tool_trace": [],
    "compliance_result": None,
    "compliance_rounds": 0,
    "final_answer": "",
    "need_human_intervention": False,
    "intervention_reason": None,
}


def _print_result(tag: str, result: dict) -> None:
    print(f"\n===== [{tag}] =====")
    print(f"intent: {result.get('intent')}")
    print(f"compliance: {(result.get('compliance_result') or {}).get('verdict')}")
    steps = result.get("agent_steps") or []
    print(f"agent_steps: {len(steps)} 步")
    for s in steps:
        print(f"  [{s['step_index']}] {s['agent']:8s} {s['status']:6s} {s['duration_ms']}ms | {s['summary'][:60]}")
    tools_used = result.get("tool_trace") or []
    print(f"used_tools: {[t['tool'] for t in tools_used]}")
    print(f"answer: {result.get('final_answer', '')[:400]}")


async def main() -> None:
    await init_db()
    await seed_policies()
    await seed_medical_records()

    executor = ToolExecutor(get_default_registry())
    from langgraph.checkpoint.memory import InMemorySaver

    checkpointer = InMemorySaver()
    graph = build_main_graph(executor=executor, checkpointer=checkpointer)
    cfg = {"configurable": {"thread_id": "verify-e2e"}}

    # ===== 场景 1：multi_step 多步任务全链路 =====
    q1 = "我做了阑尾炎手术能赔多少"
    result1 = await graph.ainvoke(
        {**RESET_INPUT, "messages": [HumanMessage(content=q1)]}, config=cfg
    )
    _print_result(f"multi_step: {q1}", result1)

    assert result1.get("intent") == "multi_step", f"意图错误：{result1.get('intent')}"
    steps = result1.get("agent_steps") or []
    assert len(steps) >= 2, f"步骤数不足：{len(steps)}"
    assert [s["agent"] for s in steps][:2] == ["medical", "claim"], "步骤顺序错误"
    assert all(s["status"] == "done" for s in steps), "存在失败步骤"
    assert result1.get("tool_trace"), "无工具调用轨迹"
    verdict = (result1.get("compliance_result") or {}).get("verdict")
    assert verdict == "PASS", f"合规状态异常：{verdict}"
    assert result1.get("final_answer"), "回答为空"
    assert "保证赔付" not in result1["final_answer"], "回答含承诺性话术"

    # ===== 场景 2：simple_faq RAG 路径（同会话第二轮，F14 上下文） =====
    q2 = "阑尾炎手术有等待期吗"
    result2 = await graph.ainvoke(
        {**RESET_INPUT, "messages": [HumanMessage(content=q2)]}, config=cfg
    )
    _print_result(f"simple_faq: {q2}", result2)

    assert result2.get("intent") == "simple_faq", f"意图错误：{result2.get('intent')}"
    rag_ctx = (result2.get("shared_data") or {}).get("rag_context", {})
    assert rag_ctx.get("results"), "RAG 检索无结果"
    assert "30" in result2.get("final_answer", ""), "回答未包含等待期天数"

    # F14：第二轮历史含第一轮消息（checkpoint 上下文连贯）
    humans = [m.content for m in result2["messages"] if isinstance(m, HumanMessage)]
    assert q1 in humans and q2 in humans, "多轮上下文丢失"

    # ===== 场景 3：F14 重启恢复（重建图实例 + 共享 checkpointer） =====
    graph_restarted = build_main_graph(executor=executor, checkpointer=checkpointer)
    q3 = "刚才我说做了什么手术？预估能赔多少？"
    result3 = await graph_restarted.ainvoke(
        {**RESET_INPUT, "messages": [HumanMessage(content=q3)]}, config=cfg
    )
    _print_result(f"重启后追问: {q3}", result3)

    humans3 = [m.content for m in result3["messages"] if isinstance(m, HumanMessage)]
    assert q1 in humans3, "重启后历史消息丢失"
    answer3 = result3.get("final_answer", "")
    assert "阑尾炎" in answer3, f"重启后未能引用历史上下文：{answer3[:200]}"

    # ===== 场景 4：chitchat ReAct 直答路径 =====
    cfg2 = {"configurable": {"thread_id": "verify-chitchat"}}
    result4 = await graph.ainvoke(
        {**RESET_INPUT, "messages": [HumanMessage(content="你好")]}, config=cfg2
    )
    _print_result("chitchat: 你好", result4)
    assert result4.get("final_answer"), "寒暄无回答"

    await dispose_engine()
    print("\nT021 验收通过：完整结构返回 / 多步全链路 / RAG 路径 / 重启恢复 / 多轮上下文")


asyncio.run(main())
