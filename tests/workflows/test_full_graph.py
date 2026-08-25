"""完整主图测试（T021：intent 分流 + 多 Agent 协作 + 合规门禁）。

覆盖（mock LLM，不耗真实 token）：
- route_intent 三分支路由
- multi_step 全链路：intent → planner → step_executor 循环 → synthesize → compliance
- simple_faq 全链路：intent → rag_node → synthesize → compliance
- A06 完整响应结构（answer/intent/used_tools/agent_steps/compliance_status）
- F14：共享 checkpointer 的两个图实例模拟"服务重启后恢复历史会话"

真实 LLM 端到端验收见 scripts/verify_e2e.py。
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import HumanMessage

import nodes.compliance as compliance_module
import nodes.generator as generator_module
import nodes.intent as intent_module
import nodes.planner as planner_module
import nodes.rag as rag_module
import nodes.step_executor as step_executor_module
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry
from workflows.main_graph import build_main_graph, route_intent

# 每轮输入的全量重置字段（与 A06 保持一致）
_RESET_INPUT = {
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


class FakeModel:
    """可控 LLM：固定响应。"""

    def __init__(self, content: str) -> None:
        self._content = content

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        class _Resp:
            content = self._content

        return _Resp()


def _patch_all(
    monkeypatch: pytest.MonkeyPatch,
    *,
    intent: str = "multi_step",
    plan: dict[str, Any] | None = None,
    compliance: str = "PASS",
) -> None:
    """统一 mock：intent / planner / 合规（synthesize 与 step_executor 单独 mock）。"""
    monkeypatch.setattr(
        intent_module, "get_chat_model", lambda *a, **k: FakeModel(f'{{"intent": "{intent}", "reason": "测试"}}')
    )
    if plan is not None:
        import json as json_mod

        monkeypatch.setattr(
            planner_module, "get_chat_model", lambda *a, **k: FakeModel(json_mod.dumps(plan, ensure_ascii=False))
        )
    monkeypatch.setattr(
        compliance_module,
        "get_chat_model",
        lambda *a, **k: FakeModel(
            f'{{"verdict": "{compliance}", "violations": [], "risk_score": 0, "reason": "测试"}}'
        ),
    )


def _make_graph() -> Any:
    from langgraph.checkpoint.memory import InMemorySaver

    return build_main_graph(
        executor=ToolExecutor(ToolRegistry()), checkpointer=InMemorySaver()
    )


# ---------- 路由 ----------


def test_route_intent_three_branches() -> None:
    assert route_intent({"intent": "multi_step"}) == "planner"
    assert route_intent({"intent": "simple_faq"}) == "rag"
    assert route_intent({"intent": "single_domain"}) == "react"
    assert route_intent({"intent": "chitchat"}) == "react"
    assert route_intent({"intent": "other"}) == "react"
    assert route_intent({}) == "react"


# ---------- multi_step 全链路 ----------


async def test_multi_step_full_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """multi_step：intent → planner（2 步）→ step_executor×2 → synthesize → compliance PASS。"""
    _patch_all(
        monkeypatch,
        intent="multi_step",
        plan={
            "steps": [
                {"agent": "medical", "description": "医疗审核"},
                {"agent": "claim", "description": "理赔核算"},
            ]
        },
    )

    # step_executor：mock run_worker_agent（顺序产出两个 Agent 结论）
    results = iter(
        [
            {"summary": "阑尾炎 K35 在保障范围内"},
            {"summary": "预估赔付 4640 元"},
        ]
    )

    async def fake_run(agent_def, instruction, shared_data, executor, tool_trace=None):  # noqa: ANN001
        return dict(next(results))

    monkeypatch.setattr(step_executor_module, "run_worker_agent", fake_run)

    # synthesize：mock 整合输出
    monkeypatch.setattr(
        generator_module,
        "get_chat_model",
        lambda *a, **k: FakeModel("综合结论：预估可赔付 4640 元，以理赔审核结果为准"),
    )

    graph = _make_graph()
    state = {**_RESET_INPUT, "messages": [HumanMessage(content="我做了阑尾炎手术能赔多少")]}
    result = await graph.ainvoke(state, config={"configurable": {"thread_id": "t-multi"}})

    assert result["intent"] == "multi_step"
    assert result["final_answer"] == "综合结论：预估可赔付 4640 元，以理赔审核结果为准"
    assert [s["agent"] for s in result["task_plan"]] == ["medical", "claim"]
    assert all(s["status"] == "done" for s in result["task_plan"])
    assert len(result["agent_steps"]) == 2
    assert result["agent_steps"][0]["agent"] == "medical"
    assert result["shared_data"]["medical"]["summary"] == "阑尾炎 K35 在保障范围内"
    assert result["shared_data"]["claim"]["summary"] == "预估赔付 4640 元"
    assert result["compliance_result"]["verdict"] == "PASS"
    assert result["need_human_intervention"] is False


async def test_multi_step_synthesize_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """synthesize LLM 故障：确定性兜底拼接各 Agent summary，不抛错。"""
    _patch_all(
        monkeypatch,
        intent="multi_step",
        plan={"steps": [{"agent": "claim", "description": "核算"}]},
    )

    async def fake_run(agent_def, instruction, shared_data, executor, tool_trace=None):  # noqa: ANN001
        return {"summary": "预估赔付 4640 元"}

    monkeypatch.setattr(step_executor_module, "run_worker_agent", fake_run)

    class _BrokenModel:
        async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
            raise RuntimeError("LLM 超时")

    monkeypatch.setattr(generator_module, "get_chat_model", lambda *a, **k: _BrokenModel())

    graph = _make_graph()
    result = await graph.ainvoke(
        {**_RESET_INPUT, "messages": [HumanMessage(content="能赔多少")]},
        config={"configurable": {"thread_id": "t-fallback"}},
    )
    assert "预估赔付 4640 元" in result["final_answer"]
    assert "以理赔审核结果为准" in result["final_answer"]


# ---------- simple_faq 全链路 ----------


async def test_simple_faq_rag_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """simple_faq：intent → rag_node（检索）→ synthesize → compliance。"""
    _patch_all(monkeypatch, intent="simple_faq")

    # mock 检索服务
    from services.rag.retriever import RetrievedChunk

    async def fake_search(query: str, top_k: int = 4):
        return [
            RetrievedChunk(
                text="疾病住院医疗等待期 30 天，等待期内确诊的疾病不承担赔付责任。",
                title="等待期规则详解",
                category="claim_rules",
                source_file="05-等待期规则详解.md",
                score=0.75,
            )
        ]

    monkeypatch.setattr(rag_module, "search_kb", fake_search)
    monkeypatch.setattr(
        generator_module,
        "get_chat_model",
        lambda *a, **k: FakeModel("根据条款，医疗险疾病等待期为 30 天，等待期内确诊不赔付。"),
    )

    graph = _make_graph()
    result = await graph.ainvoke(
        {**_RESET_INPUT, "messages": [HumanMessage(content="阑尾炎手术有等待期吗")]},
        config={"configurable": {"thread_id": "t-faq"}},
    )

    assert result["intent"] == "simple_faq"
    assert result["final_answer"].startswith("根据条款")
    rag_ctx = result["shared_data"]["rag_context"]
    assert rag_ctx["summary"] == "知识库检索到 1 条相关条款"
    assert rag_ctx["results"][0]["title"] == "等待期规则详解"
    assert result["compliance_result"]["verdict"] == "PASS"


async def test_simple_faq_rag_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """检索无结果：rag_context 标记空，流程继续不报错。"""
    _patch_all(monkeypatch, intent="simple_faq")

    async def fake_search(query: str, top_k: int = 4):
        return []

    monkeypatch.setattr(rag_module, "search_kb", fake_search)
    monkeypatch.setattr(
        generator_module, "get_chat_model", lambda *a, **k: FakeModel("抱歉，暂未查到相关条款。")
    )

    graph = _make_graph()
    result = await graph.ainvoke(
        {**_RESET_INPUT, "messages": [HumanMessage(content="奇怪的规则问题")]},
        config={"configurable": {"thread_id": "t-faq-empty"}},
    )
    assert result["shared_data"]["rag_context"]["summary"] == "知识库检索无结果"
    assert result["compliance_result"]["verdict"] == "PASS"


async def test_simple_faq_rag_error_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """检索服务故障：不抛错，synthesize 走兜底。"""
    _patch_all(monkeypatch, intent="simple_faq")

    async def broken_search(query: str, top_k: int = 4):
        raise RuntimeError("Qdrant 不可用")

    monkeypatch.setattr(rag_module, "search_kb", broken_search)

    class _BrokenModel:
        async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
            raise RuntimeError("LLM 超时")

    monkeypatch.setattr(generator_module, "get_chat_model", lambda *a, **k: _BrokenModel())

    graph = _make_graph()
    result = await graph.ainvoke(
        {**_RESET_INPUT, "messages": [HumanMessage(content="等待期是多久")]},
        config={"configurable": {"thread_id": "t-faq-err"}},
    )
    # T032 混合召回：向量检索故障降级为 0 条（不致命），本地图谱仍补充事实
    rag_ctx = result["shared_data"]["rag_context"]
    assert rag_ctx["summary"] == "知识库检索到 0 条相关条款"
    assert "graph_facts" in rag_ctx
    assert result["compliance_result"]["verdict"] == "PASS"


# ---------- F14：重启后恢复（共享 checkpointer 的两个图实例） ----------


async def test_restart_recovers_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """F14 语义验证：图实例销毁重建（模拟服务重启），同 thread 历史可继续。"""
    from langgraph.checkpoint.memory import InMemorySaver

    _patch_all(
        monkeypatch,
        intent="multi_step",
        plan={"steps": [{"agent": "claim", "description": "核算"}]},
    )

    async def fake_run(agent_def, instruction, shared_data, executor, tool_trace=None):  # noqa: ANN001
        return {"summary": "预估赔付 4640 元"}

    monkeypatch.setattr(step_executor_module, "run_worker_agent", fake_run)
    monkeypatch.setattr(
        generator_module, "get_chat_model", lambda *a, **k: FakeModel("第一轮回答：预估 4640 元")
    )

    checkpointer = InMemorySaver()  # 模拟持久层（prod 为 PostgreSQL）
    graph1 = build_main_graph(executor=ToolExecutor(ToolRegistry()), checkpointer=checkpointer)
    cfg = {"configurable": {"thread_id": "t-restart"}}

    await graph1.ainvoke(
        {**_RESET_INPUT, "messages": [HumanMessage(content="能赔多少")]}, config=cfg
    )

    # 模拟重启：新图实例 + 同一 checkpointer（prod 下为同一 PostgreSQL）
    monkeypatch.setattr(
        generator_module, "get_chat_model", lambda *a, **k: FakeModel("第二轮回答：引用了第一轮的 4640 元")
    )
    graph2 = build_main_graph(executor=ToolExecutor(ToolRegistry()), checkpointer=checkpointer)
    result = await graph2.ainvoke(
        {**_RESET_INPUT, "messages": [HumanMessage(content="刚才说的金额是多少")]}, config=cfg
    )

    # checkpoint 恢复：第二轮消息历史含第一轮全部消息
    messages = result["messages"]
    human_contents = [m.content for m in messages if isinstance(m, HumanMessage)]
    assert "能赔多少" in human_contents
    assert "刚才说的金额是多少" in human_contents
    assert result["final_answer"] == "第二轮回答：引用了第一轮的 4640 元"
    # 每轮字段已重置（agent_steps 为本轮，非跨轮累积）
    assert len(result["agent_steps"]) == 1
