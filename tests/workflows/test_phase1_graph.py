"""Phase 1 主图与 A06 发消息测试（mock LLM，不耗真实 token）。

- 图结构：react_agent 循环 + 最终回答（should_continue 条件边）
- A06 协议：answer / used_tools / 审计落库 / 404 / 每轮轨迹重置
真实 LLM 端到端验收已在 T012 执行记录（progress.md）：F07/F14 实测通过。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
from app.main import app
from services.db.models import Base
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

    def bind_tools(self, specs: list[Any]) -> ScriptedLLM:
        self._bound_specs = specs
        return self


@pytest.fixture()
def graph_env(monkeypatch):
    """注册中心 + mock LLM + InMemorySaver 图。"""
    from tests.tools.test_infrastructure import EchoTool

    registry = ToolRegistry()
    registry.register(EchoTool())

    import nodes.generator as generator_module

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

    from langgraph.checkpoint.memory import InMemorySaver

    from tools.executor import ToolExecutor
    from workflows.main_graph import build_phase1_graph

    graph = build_phase1_graph(executor=ToolExecutor(registry), checkpointer=InMemorySaver())
    return graph, scripted


async def test_graph_react_loop_executes_tools_and_finishes(graph_env) -> None:
    """图结构：工具循环 → ToolMessage 回填 → 最终回答。"""
    graph, scripted = graph_env
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="你好")], "tool_trace": []},
        config={"configurable": {"thread_id": "t-1"}},
    )

    assert result["final_answer"] == "工具结果是 hi，这是最终回答"
    assert len(result["tool_trace"]) == 1
    assert result["tool_trace"][0]["tool"] == "echo"
    assert result["tool_trace"][0]["output"]["success"] is True
    # 消息序列：human → ai(tool_calls) → tool → ai(final)
    types = [type(m).__name__ for m in result["messages"]]
    assert types == ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage"]


async def test_graph_multi_turn_same_thread(graph_env) -> None:
    """F14（图层级）：同 thread 第二轮携带历史（checkpoint 生效）。"""
    graph, scripted = graph_env
    cfg = {"configurable": {"thread_id": "t-multi"}}

    await graph.ainvoke({"messages": [HumanMessage(content="第一轮")], "tool_trace": []}, config=cfg)

    # 补充第二轮脚本（脚本已耗尽，追加）
    scripted._responses.append(AIMessage(content="第二轮回答"))

    result2 = await graph.ainvoke(
        {"messages": [HumanMessage(content="第二轮")], "tool_trace": []}, config=cfg
    )
    # 第二轮 LLM 输入包含第一轮全部历史（含最终回答）
    last_call_messages = scripted.calls[-1]
    human_contents = [m.content for m in last_call_messages if isinstance(m, HumanMessage)]
    assert "第一轮" in human_contents
    assert "第二轮" in human_contents
    assert result2["final_answer"] == "第二轮回答"


def test_should_continue_routing() -> None:
    """条件边：末尾 ToolMessage → tools；末尾 AIMessage → end。"""
    from nodes.generator import should_continue

    state_with_tool = {
        "messages": [HumanMessage(content="q"), AIMessage(content=""), ToolMessage(content="r", tool_call_id="x")],
        "tool_trace": [{"tool": "echo"}],
    }
    assert should_continue(state_with_tool) == "tools"

    state_final = {"messages": [HumanMessage(content="q"), AIMessage(content="done")]}
    assert should_continue(state_final) == "end"

    # 超过轮数上限强制结束
    state_over = {
        "messages": [HumanMessage(content="q"), ToolMessage(content="r", tool_call_id="x")],
        "tool_trace": [{"tool": f"t{i}"} for i in range(8)],
    }
    assert should_continue(state_over) == "end"


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
    """A06：answer + used_tools 轨迹 + 审计落库。"""
    ac, graph, _ = api_client
    conv = (await ac.post("/api/v1/conversations", json={})).json()
    cid = conv["conversation_id"]

    resp = await ac.post(f"/api/v1/conversations/{cid}/messages", json={"content": "你好"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "工具结果是 hi，这是最终回答"
    assert len(body["used_tools"]) == 1
    assert body["used_tools"][0]["tool"] == "echo"

    # 审计落库：2 条消息，assistant 带 tool_trace
    history = (await ac.get(f"/api/v1/conversations/{cid}/messages")).json()
    assert history["total"] == 2
    assistant = history["items"][1]
    assert assistant["role"] == "assistant"
    assert assistant["tool_trace"][0]["tool"] == "echo"


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
