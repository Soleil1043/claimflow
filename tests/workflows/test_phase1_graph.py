"""Phase 1 主图与 A06 发消息测试（mock LLM，不耗真实 token；T047 适配 react 子图）。

- 图结构：react create_agent 子图（工具循环内置）→ 最终回答
- A06 协议：answer / used_tools（messages 派生）/ 审计落库 / 404
真实 LLM 端到端验收已在 T012 执行记录（progress.md）：F07/F14 实测通过。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, PrivateAttr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import nodes.generator as generator_module
import services.db.session as session_module
from app.main import app
from services.db.models import Base
from tools.base import ClaimflowTool
from tools.factory import assemble_tool
from tools.registry import ToolRegistry

# ---------- 可控 Fake LLM：脚本化响应序列 ----------


class ScriptedLLM:
    """按脚本依次返回响应（先工具调用，再最终回答）。"""

    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = list(responses)
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any], config: Any = None) -> AIMessage:
        self.calls.append(messages)
        return self._responses.pop(0)

    def bind_tools(self, specs: list[Any], **kwargs: Any) -> ScriptedLLM:
        self._bound_specs = specs
        return self

    def bind(self, **kwargs: Any) -> ScriptedLLM:
        return self


# ---------- 工厂桩工具（echo，供 react 子图工具循环） ----------


class _EchoIn(BaseModel):
    text: str = "hi"


class _EchoTool(ClaimflowTool):
    name: str = "echo"
    description: str = "测试用回显工具"
    args_schema: type[_EchoIn] = _EchoIn
    _calls: int = PrivateAttr(default=0)

    def _run(self, *args: object, **kwargs: object) -> dict:
        raise NotImplementedError("仅支持异步调用")

    async def _arun(self, *, text: str = "hi") -> dict:
        self._calls += 1
        return {"success": True, "echo": text}


@pytest.fixture()
def graph_env(monkeypatch):
    """工厂桩工具 + mock LLM + InMemorySaver 图（完整图，intent 走 react 路径）。"""
    registry = ToolRegistry()
    # 合规工具（T018：图输出必经 compliance 节点，ComplianceNode 经 executor 取证）
    from tools.compliance import ComplianceRuleCheckTool, RiskScoringTool

    registry.register(ComplianceRuleCheckTool())
    registry.register(RiskScoringTool())

    import nodes.compliance as compliance_module
    import nodes.intent as intent_module

    # 意图分类 LLM：固定 single_domain（走 react 路径；with_structured_output 按 schema 解析）
    class _StructuredMixin:
        _content: str = ""

        def with_structured_output(self, schema: Any, method: str | None = None) -> Any:
            assert method == "function_calling"

            class _Structured:
                def __init__(self, content: str, schema: Any) -> None:
                    self._content = content
                    self._schema = schema

                async def ainvoke(self, messages: list[Any], **kwargs: Any) -> Any:
                    import json as _json

                    return self._schema.model_validate(_json.loads(self._content))

            return _Structured(self._content, schema)

    class _IntentModel(_StructuredMixin):
        _content = '{"intent": "single_domain", "reason": "查数据"}'

        async def ainvoke(self, messages: list[Any], config: Any = None) -> AIMessage:
            return AIMessage(content=self._content)

    monkeypatch.setattr(intent_module, "get_chat_model", lambda *a, **k: _IntentModel())

    scripted = ScriptedLLM(
        [
            # 第一轮：请求工具
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "echo", "args": {"text": "hi"}, "id": "call_1"}
                ],
            ),
            # 第二轮：最终回答
            AIMessage(content="工具结果是 hi，这是最终回答"),
        ]
    )
    monkeypatch.setattr(generator_module, "get_chat_model", lambda: scripted)
    # react 子图工具图：桩 echo 替换（避免真查 DB）；缓存重建拾取
    patched_map = {
        **generator_module.get_default_tool_map(),
        "echo": assemble_tool(_EchoTool(), enable_cache=False),
    }
    monkeypatch.setattr(generator_module, "get_default_tool_map", lambda: patched_map)
    # A06 used_tools 派生白名单同样取自工厂（保持与图上工具一致，echo 才能入列）
    import tools.factory as factory_module

    monkeypatch.setattr(factory_module, "get_default_tool_map", lambda: patched_map)
    monkeypatch.setattr(generator_module, "_react_agent", None)

    # 合规审查 LLM：固定返回 PASS（回答无违规，走直通路径；结构化输出同上）
    class _PassModel(_StructuredMixin):
        _content = '{"verdict": "PASS", "violations": [], "risk_score": 0, "reason": "无违规"}'

        async def ainvoke(self, messages: list[Any], config: Any = None) -> AIMessage:
            return AIMessage(content=self._content)

    monkeypatch.setattr(compliance_module, "get_chat_model", lambda *a, **k: _PassModel())

    from langgraph.checkpoint.memory import InMemorySaver

    from tools.executor import ToolExecutor
    from workflows.main_graph import build_main_graph

    graph = build_main_graph(executor=ToolExecutor(registry), checkpointer=InMemorySaver())
    return graph, scripted


async def test_graph_react_loop_executes_tools_and_finishes(graph_env) -> None:
    """图结构：react 子图内工具循环 → 工具回执 → 最终回答（轨迹 messages 派生）。"""
    from agents.runner import derive_tool_trace

    graph, scripted = graph_env
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="你好")]},
        config={"configurable": {"thread_id": "t-1"}},
    )

    assert result["final_answer"] == "工具结果是 hi，这是最终回答"
    used = derive_tool_trace(result["messages"])
    assert len(used) == 1
    assert used[0]["tool"] == "echo"
    assert used[0]["output"]["success"] is True
    # 消息序列：human → ai(tool_calls) → tool → ai(final)
    types = [type(m).__name__ for m in result["messages"]]
    assert types == ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage"]


async def test_graph_multi_turn_same_thread(graph_env) -> None:
    """F14（图层级）：同 thread 第二轮携带历史（checkpoint 生效）。"""
    graph, scripted = graph_env
    cfg = {"configurable": {"thread_id": "t-multi"}}

    await graph.ainvoke({"messages": [HumanMessage(content="第一轮")]}, config=cfg)

    # 补充第二轮脚本（脚本已耗尽，追加）
    scripted._responses.append(AIMessage(content="第二轮回答"))

    result2 = await graph.ainvoke(
        {"messages": [HumanMessage(content="第二轮")]}, config=cfg
    )
    # 第二轮 LLM 输入包含第一轮全部历史（含最终回答）
    last_call_messages = scripted.calls[-1]
    human_contents = [m.content for m in last_call_messages if isinstance(m, HumanMessage)]
    assert "第一轮" in human_contents
    assert "第二轮" in human_contents
    assert result2["final_answer"] == "第二轮回答"


# ---------- A06 API 集成（mock LLM） ----------


@pytest.fixture()
async def api_client(monkeypatch, graph_env):
    """内存 SQLite + mock 图的 A06 测试客户端。

    ASGITransport 不触发 lifespan，直接给 app.state.graph 赋值
    （get_app_graph 从 request.app.state 读取）。
    """
    graph, scripted = graph_env

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "_engine", engine)
    monkeypatch.setattr(session_module, "_session_factory", factory)
    monkeypatch.setattr(session_module.settings, "llm_api_key", "sk-test")

    app.state.graph = graph
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, graph, scripted
    # 清理 app.state，避免污染其他测试
    if hasattr(app.state, "graph"):
        delattr(app.state, "graph")
    await engine.dispose()


async def test_a06_send_message_returns_answer_and_tools(api_client) -> None:
    """A06：answer + used_tools（messages 派生）轨迹 + 审计落库。"""
    from agents.runner import derive_tool_trace

    ac, graph, _ = api_client
    conv = (await ac.post("/api/v1/conversations", json={})).json()
    cid = conv["conversation_id"]

    resp = await ac.post(f"/api/v1/conversations/{cid}/messages", json={"content": "你好"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "工具结果是 hi，这是最终回答"
    assert len(body["used_tools"]) == 1
    assert body["used_tools"][0]["tool"] == "echo"

    # 审计落库：2 条消息，assistant 带 tool_trace（与响应 used_tools 同源派生）
    history = (await ac.get(f"/api/v1/conversations/{cid}/messages")).json()
    assert history["total"] == 2
    assistant = history["items"][1]
    assert assistant["role"] == "assistant"
    assert assistant["tool_trace"][0]["tool"] == "echo"
    # 派生口径与响应一致
    assert [t["tool"] for t in derive_tool_trace([])] == []


async def test_a06_conversation_not_found(api_client) -> None:
    """A06：不存在的会话 404。"""
    ac, _, _ = api_client
    resp = await ac.post(
        f"/api/v1/conversations/{uuid.uuid4()}/messages", json={"content": "你好"}
    )
    assert resp.status_code == 404


async def test_a06_validates_empty_content(api_client) -> None:
    """A06：空消息 422。"""
    ac, _, _ = api_client
    conv = (await ac.post("/api/v1/conversations", json={})).json()
    resp = await ac.post(f"/api/v1/conversations/{conv['conversation_id']}/messages", json={"content": ""})
    assert resp.status_code == 422
