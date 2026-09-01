"""完整主图测试（T021/T047：intent 分流 + supervisor 动态调度 + 合规门禁）。

覆盖（mock LLM，不耗真实 token）：
- route_intent 三分支路由
- complex_consult 全链路：intent → supervisor ⇄ worker 子图 → synthesize → compliance
- simple_faq 全链路：intent → rag_node → synthesize → compliance
- A06 派生口径（used_tools 从 messages、agent_steps 从 task_plan 推导）
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
import nodes.rag as rag_module
import nodes.supervisor as supervisor_module
from agents.runner import derive_tool_trace
from nodes.supervisor import derive_agent_steps
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry
from workflows.main_graph import build_main_graph, route_intent

# 每轮输入的全量重置字段（与 A06 保持一致；T047 起 State 无簿记字段）
_RESET_INPUT = {
    "intent": None,
    "task_plan": [],
    "shared_data": {},
    "compliance_result": None,
    "compliance_rounds": 0,
    "final_answer": "",
    "need_human_intervention": False,
    "intervention_reason": None,
}


class FakeModel:
    """可控 LLM：ainvoke 返回预设文本；with_structured_output 按 schema 解析（T045）。"""

    def __init__(self, content: str) -> None:
        self._content = content

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        class _Resp:
            content = self._content

        return _Resp()

    def with_structured_output(self, schema: Any, method: str | None = None) -> Any:
        assert method == "function_calling"
        return _Structured(self._content, schema)


class _Structured:
    """结构化链路假件：预设 JSON 经 schema 校验返回实例（与真实 function calling 链路同构）。"""

    def __init__(self, content: str, schema: Any) -> None:
        self._content = content
        self._schema = schema

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        import json as _json

        return self._schema.model_validate(_json.loads(self._content))


def _patch_all(
    monkeypatch: pytest.MonkeyPatch,
    *,
    intent: str = "complex_consult",
    routing: dict[str, Any] | None = None,
    compliance: str = "PASS",
) -> None:
    """统一 mock：intent / supervisor 路由 / 合规（synthesize 与 worker 单独 mock）。

    routing 为静态 RoutingDecision——多轮调度靠 supervisor 对账守卫收敛
    （已 done 的目标自动改投首个 pending，全部 done → FINISH）。
    """
    monkeypatch.setattr(
        intent_module, "get_chat_model", lambda *a, **k: FakeModel(f'{{"intent": "{intent}", "reason": "测试"}}')
    )
    if routing is not None:
        import json as json_mod

        decision = {
            "next": routing.get("next", "medical"),
            "plan": routing["plan"],
            "reason": "测试",
        }
        monkeypatch.setattr(
            supervisor_module,
            "get_chat_model",
            lambda *a, **k: FakeModel(json_mod.dumps(decision, ensure_ascii=False)),
        )
    monkeypatch.setattr(
        compliance_module,
        "get_chat_model",
        lambda *a, **k: FakeModel(
            f'{{"verdict": "{compliance}", "violations": [], "risk_score": 0, "reason": "测试"}}'
        ),
    )


def _patch_workers(monkeypatch: pytest.MonkeyPatch, results: list[dict[str, Any]]) -> None:
    """worker 子图打桩：invoke_worker 循环产出结论（多轮/重入不耗尽）。"""
    import itertools

    pending = itertools.cycle(results)

    async def fake_invoke(agent_def, instruction, shared_data):  # noqa: ANN001
        return dict(next(pending)), []

    monkeypatch.setattr(supervisor_module, "invoke_worker", fake_invoke)


def _make_graph() -> Any:
    from langgraph.checkpoint.memory import InMemorySaver

    return build_main_graph(
        executor=ToolExecutor(ToolRegistry()), checkpointer=InMemorySaver()
    )


# ---------- 路由 ----------


def test_route_intent_three_branches() -> None:
    assert route_intent({"intent": "complex_consult"}) == "supervisor"
    assert route_intent({"intent": "simple_faq"}) == "rag"
    assert route_intent({"intent": "single_domain"}) == "react"
    assert route_intent({"intent": "chitchat"}) == "react"
    assert route_intent({"intent": "other"}) == "react"
    assert route_intent({}) == "react"


# ---------- complex_consult 全链路 ----------


async def test_complex_consult_full_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """complex_consult：intent → supervisor ⇄ medical/claim → synthesize → compliance PASS。"""
    _patch_all(
        monkeypatch,
        intent="complex_consult",
        routing={
            "next": "medical",
            "plan": [
                {"agent": "medical", "description": "医疗审核"},
                {"agent": "claim", "description": "理赔核算"},
            ],
        },
    )
    _patch_workers(
        monkeypatch,
        [
            {"summary": "阑尾炎 K35 在保障范围内"},
            {"summary": "预估赔付 4640 元"},
        ],
    )

    # synthesize：mock 整合输出
    monkeypatch.setattr(
        generator_module,
        "get_chat_model",
        lambda *a, **k: FakeModel("综合结论：预估可赔付 4640 元，以理赔审核结果为准"),
    )

    graph = _make_graph()
    state = {**_RESET_INPUT, "messages": [HumanMessage(content="我做了阑尾炎手术能赔多少")]}
    result = await graph.ainvoke(state, config={"configurable": {"thread_id": "t-multi"}})

    assert result["intent"] == "complex_consult"
    assert result["final_answer"] == "综合结论：预估可赔付 4640 元，以理赔审核结果为准"
    plan = result["task_plan"]
    assert [s["agent"] for s in plan] == ["medical", "claim"]
    assert all(s["status"] == "done" for s in plan)
    # A06 派生口径：agent_steps 由 task_plan 推导
    steps = derive_agent_steps(plan, result["shared_data"])
    assert len(steps) == 2
    assert steps[0]["agent"] == "medical"
    assert steps[0]["status"] == "done"
    assert result["shared_data"]["medical"]["summary"] == "阑尾炎 K35 在保障范围内"
    assert result["shared_data"]["claim"]["summary"] == "预估赔付 4640 元"
    assert result["compliance_result"]["verdict"] == "PASS"
    assert result["need_human_intervention"] is False


async def test_complex_consult_synthesize_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """synthesize LLM 故障：确定性兜底拼接各 Agent summary，不抛错。"""
    _patch_all(
        monkeypatch,
        intent="complex_consult",
        routing={"next": "claim", "plan": [{"agent": "claim", "description": "核算"}]},
    )
    _patch_workers(monkeypatch, [{"summary": "预估赔付 4640 元"}])

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
        intent="complex_consult",
        routing={"next": "claim", "plan": [{"agent": "claim", "description": "核算"}]},
    )
    _patch_workers(monkeypatch, [{"summary": "预估赔付 4640 元"}])
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
    # 每轮字段已重置（task_plan 为本轮，非跨轮累积）
    assert len(derive_agent_steps(result["task_plan"], result["shared_data"])) == 1


# ---------- react 路径（T047：create_agent 子图） ----------


async def test_react_path_with_tool_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """single_domain：react 子图内完成工具循环 → 轨迹随 messages 派生。"""
    from langchain_core.messages import AIMessage

    _patch_all(monkeypatch, intent="single_domain")

    class ScriptedModel:
        """两轮脚本：请求工具 → 终答（bind_tools 透传，记录调用）。"""

        def __init__(self) -> None:
            self.calls: list[list[Any]] = []

        def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedModel:
            return self

        def bind(self, **kwargs: Any) -> ScriptedModel:
            return self

        async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
            self.calls.append(list(messages))
            if len(self.calls) == 1:
                return AIMessage(
                    content="",
                    tool_calls=[{"name": "policy_query", "args": {"policy_no": "POL-2025-0001"}, "id": "c1"}],
                )
            return AIMessage(content="您的保单 POL-2025-0001 状态正常。")

    scripted = ScriptedModel()
    monkeypatch.setattr(generator_module, "get_chat_model", lambda *a, **k: scripted)
    # react 工具循环内不真查 DB：桩掉工具（守卫装配后的 policy_query）。
    # 注意 get_react_agent 经 generator 命名空间引用工厂（from-import 绑定），须 patch 此处

    from tools.base import ClaimflowTool
    from tools.claim.policy_query import PolicyQueryInput
    from tools.factory import assemble_tool

    class _FakePolicyQuery(ClaimflowTool):
        name: str = "policy_query"
        description: str = "测试桩"
        args_schema: type[PolicyQueryInput] = PolicyQueryInput

        def _run(self, *args: object, **kwargs: object) -> dict:
            raise NotImplementedError("仅支持异步调用")

        async def _arun(self, *, policy_no: str | None = None, id_card: str | None = None) -> dict:
            return {"success": True, "policy_no": policy_no or "POL-2025-0001", "status": "active"}

    fake_tool = assemble_tool(_FakePolicyQuery(), enable_cache=False)
    patched = {**generator_module.get_default_tool_map(), "policy_query": fake_tool}
    monkeypatch.setattr(generator_module, "get_default_tool_map", lambda: patched)
    # react 子图缓存需重建以拾取桩工具
    monkeypatch.setattr(generator_module, "_react_agent", None)

    graph = _make_graph()
    result = await graph.ainvoke(
        {**_RESET_INPUT, "messages": [HumanMessage(content="查一下保单 POL-2025-0001")]},
        config={"configurable": {"thread_id": "t-react"}},
    )

    assert result["intent"] == "single_domain"
    assert result["final_answer"] == "您的保单 POL-2025-0001 状态正常。"
    # 轨迹从 messages 派生（A06 口径）
    used = derive_tool_trace(result["messages"])
    assert [t["tool"] for t in used] == ["policy_query"]
    assert used[0]["agent"] == "react"
