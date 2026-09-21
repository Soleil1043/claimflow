"""门户客服 API 测试（T134）：建会话 / 状态 / 历史 / 发消息（ai 应答、escalated 停答、closed 409、LLM 故障 503）。"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
import services.support.agent as sa
from app.main import app
from services.db.models import Base
from services.db.session import dispose_engine
from services.support import store
from services.support.agent import reset_support_agent_cache
from tests.support.test_agent import _fake_tool_map


class ScriptedModel(GenericFakeChatModel):
    """脚本化模型：忽略工具绑定（按预排消息应答）。"""

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedModel:  # noqa: ARG002
        return self


class BoomModel(GenericFakeChatModel):
    """必然故障模型（LLM 不可用路径）。"""

    def bind_tools(self, tools: Any, **kwargs: Any) -> BoomModel:  # noqa: ARG002
        return self

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Any:
        raise RuntimeError("llm down")


def _tool_call_msg() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "escalate_to_human",
                "args": {"reason": "客户要求人工"},
                "id": "c1",
                "type": "tool_call",
            }
        ],
    )


@pytest.fixture()
async def client(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """文件库 + 脚本化模型客户端；脚本可按测试重设（set_script），agent 缓存复位。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'support_api_test.db').as_posix()}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module.swap_engine(engine, factory)

    script: list[AIMessage] = [AIMessage(content="您好，请问有什么可以帮您？")]

    def set_script(messages: list[AIMessage]) -> None:
        script.clear()
        script.extend(messages)
        reset_support_agent_cache()

    monkeypatch.setattr(
        sa, "get_chat_model", lambda temperature=0.1: ScriptedModel(messages=iter(script))
    )
    monkeypatch.setattr(sa, "get_default_tool_map", _fake_tool_map)
    reset_support_agent_cache()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, set_script
    reset_support_agent_cache()
    await engine.dispose()
    await dispose_engine()


async def _create(ac: AsyncClient) -> str:
    resp = await ac.post("/api/v1/support/conversations")
    assert resp.status_code == 201
    return resp.json()["conversation_id"]


async def test_create_conversation(client) -> None:
    """建会话：201，uuid hex 主键，初始 ai。"""
    ac, _ = client
    resp = await ac.post("/api/v1/support/conversations")
    body = resp.json()
    assert resp.status_code == 201
    assert len(body["conversation_id"]) == 32
    assert body["status"] == "ai"
    assert body["created_at"]


async def test_roundtrip_status_and_history(client) -> None:
    """普通轮：发消息返回 AI 回复；状态/历史口径一致。"""
    ac, _ = client
    conv_id = await _create(ac)

    sent = await ac.post(
        f"/api/v1/support/conversations/{conv_id}/messages", json={"content": "你好"}
    )
    assert sent.status_code == 200
    assert sent.json() == {
        "status": "ai",
        "reply": "您好，请问有什么可以帮您？",
    }

    status = (await ac.get(f"/api/v1/support/conversations/{conv_id}")).json()
    assert status["status"] == "ai"
    assert status["escalated_reason"] is None

    history = (
        await ac.get(f"/api/v1/support/conversations/{conv_id}/messages")
    ).json()
    assert history["total"] == 2
    assert [(m["role"], m["content"]) for m in history["items"]] == [
        ("user", "你好"),
        ("assistant", "您好，请问有什么可以帮您？"),
    ]


async def test_escalated_stops_ai(client) -> None:
    """转人工轮：响应终态 escalated + 转接话术；此后发消息 reply=None（AI 停答）。"""
    ac, set_script = client
    conv_id = await _create(ac)
    set_script(
        [
            _tool_call_msg(),
            AIMessage(content="已为您转接人工客服，请保持会话开启。"),
        ]
    )

    escalated = await ac.post(
        f"/api/v1/support/conversations/{conv_id}/messages",
        json={"content": "给我转人工"},
    )
    assert escalated.status_code == 200
    assert escalated.json() == {
        "status": "escalated",
        "reply": "已为您转接人工客服，请保持会话开启。",
    }

    detail = (await ac.get(f"/api/v1/support/conversations/{conv_id}")).json()
    assert detail["status"] == "escalated"
    assert detail["escalated_reason"] == "客户要求人工"
    assert detail["escalated_at"]

    followup = await ac.post(
        f"/api/v1/support/conversations/{conv_id}/messages",
        json={"content": "好的，我等"},
    )
    assert followup.status_code == 200
    assert followup.json() == {"status": "escalated", "reply": None}

    history = (
        await ac.get(f"/api/v1/support/conversations/{conv_id}/messages")
    ).json()
    # AI 停答：escalated 后只有新增 user 消息，无 assistant
    assert [m["role"] for m in history["items"]] == ["user", "assistant", "user"]


async def test_send_message_not_found(client) -> None:
    ac, _ = client
    resp = await ac.post(
        "/api/v1/support/conversations/nope/messages", json={"content": "你好"}
    )
    assert resp.status_code == 404
    assert (await ac.get("/api/v1/support/conversations/nope")).status_code == 404
    assert (
        await ac.get("/api/v1/support/conversations/nope/messages")
    ).status_code == 404


async def test_closed_conflict(client) -> None:
    """closed 终态：发消息 409。"""
    ac, _ = client
    conv_id = await _create(ac)
    await store.close_conversation(conv_id)
    resp = await ac.post(
        f"/api/v1/support/conversations/{conv_id}/messages", json={"content": "在吗"}
    )
    assert resp.status_code == 409


async def test_llm_failure_503_message_persisted(client, monkeypatch) -> None:
    """LLM 故障：503，但用户消息已落库（时间线不丢，下一条重试）。"""
    ac, _ = client
    monkeypatch.setattr(sa, "get_chat_model", lambda temperature=0.1: BoomModel())
    reset_support_agent_cache()
    conv_id = await _create(ac)

    resp = await ac.post(
        f"/api/v1/support/conversations/{conv_id}/messages", json={"content": "你好"}
    )
    assert resp.status_code == 503

    history = (
        await ac.get(f"/api/v1/support/conversations/{conv_id}/messages")
    ).json()
    assert [(m["role"], m["content"]) for m in history["items"]] == [("user", "你好")]
