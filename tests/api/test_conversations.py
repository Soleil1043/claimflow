"""会话管理 API 测试（A02-A05，F02/F14）。

内存 SQLite + httpx ASGITransport，覆盖：
- 创建会话返回 201 与 conversation_id
- 列表分页 + message_count 计数
- 详情 404 / 正常返回最近消息摘要
- 消息历史正序 + tool_trace 等审计字段往返
- checkpointer 工厂 dev/prod 分流
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
import services.memory.short_term as short_term_module
from app.main import app
from services.db.models import Base, Message


@pytest.fixture()
async def client(monkeypatch):
    """内存 SQLite 引擎 + 测试客户端。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "_engine", engine)
    monkeypatch.setattr(session_module, "_session_factory", factory)
    monkeypatch.setattr(session_module.settings, "llm_api_key", "sk-test")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await engine.dispose()


async def _create_messages(factory, conversation_id: uuid.UUID, n: int) -> None:
    """直插 n 条消息（模拟历史对话）。"""
    async with factory() as session:
        session.add_all(
            [
                Message(
                    conversation_id=conversation_id,
                    role="user" if i % 2 == 0 else "assistant",
                    content=f"消息{i}",
                    intent="faq" if i % 2 else None,
                    tool_trace=[{"tool": "policy_query", "duration_ms": 30}] if i == 1 else None,
                    compliance_status="PASS" if i % 2 else None,
                )
                for i in range(n)
            ]
        )
        await session.commit()


# ---------- A02 创建会话 ----------


async def test_create_conversation_returns_201(client: AsyncClient) -> None:
    """A02：创建会话返回 201 + conversation_id（UUID）。"""
    resp = await client.post("/api/v1/conversations", json={"user_id": "user-a"})
    assert resp.status_code == 201
    body = resp.json()
    assert uuid.UUID(body["conversation_id"])  # 合法 UUID
    assert body["user_id"] == "user-a"
    assert body["status"] == "active"


async def test_create_conversation_default_user(client: AsyncClient) -> None:
    """A02：不传 user_id 时默认 demo-user。"""
    resp = await client.post("/api/v1/conversations", json={})
    assert resp.status_code == 201
    assert resp.json()["user_id"] == "demo-user"


async def test_create_conversation_validates_user_id(client: AsyncClient) -> None:
    """A02：空 user_id 被 422 拒绝。"""
    resp = await client.post("/api/v1/conversations", json={"user_id": ""})
    assert resp.status_code == 422


# ---------- A03 列表 ----------


async def test_list_conversations_with_pagination(client: AsyncClient) -> None:
    """A03：列表按创建时间倒序 + 分页。"""
    ids = []
    for i in range(3):
        resp = await client.post("/api/v1/conversations", json={"user_id": f"u{i}"})
        ids.append(resp.json()["conversation_id"])

    resp = await client.get("/api/v1/conversations", params={"limit": 2, "offset": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["items"][0]["user_id"] == "u2"  # 最新在前

    resp2 = await client.get("/api/v1/conversations", params={"limit": 2, "offset": 2})
    assert len(resp2.json()["items"]) == 1


async def test_list_conversations_message_count(client: AsyncClient) -> None:
    """A03：message_count 正确统计（无消息为 0）。"""
    resp = await client.post("/api/v1/conversations", json={"user_id": "u-count"})
    conv_id = uuid.UUID(resp.json()["conversation_id"])
    await _create_messages(session_module.get_session_factory(), conv_id, 4)

    body = (await client.get("/api/v1/conversations")).json()
    target = next(i for i in body["items"] if i["id"] == str(conv_id))
    assert target["message_count"] == 4


# ---------- A04 详情 ----------


async def test_get_conversation_detail_with_recent_messages(client: AsyncClient) -> None:
    """A04：详情附最近 5 条消息（时间正序）。"""
    resp = await client.post("/api/v1/conversations", json={"user_id": "u-detail"})
    conv_id = resp.json()["conversation_id"]
    await _create_messages(session_module.get_session_factory(), uuid.UUID(conv_id), 7)

    resp = await client.get(f"/api/v1/conversations/{conv_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == conv_id
    assert len(body["last_messages"]) == 5
    contents = [m["content"] for m in body["last_messages"]]
    assert contents == [f"消息{i}" for i in range(2, 7)]  # 最近 5 条，正序


async def test_get_conversation_not_found(client: AsyncClient) -> None:
    """A04：不存在的会话 404。"""
    resp = await client.get(f"/api/v1/conversations/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "会话不存在"


# ---------- A05 消息历史 ----------


async def test_list_messages_ordered_with_audit_fields(client: AsyncClient) -> None:
    """A05：消息历史正序 + tool_trace/intent/compliance 审计字段往返。"""
    resp = await client.post("/api/v1/conversations", json={"user_id": "u-msgs"})
    conv_id = resp.json()["conversation_id"]
    await _create_messages(session_module.get_session_factory(), uuid.UUID(conv_id), 3)

    resp = await client.get(f"/api/v1/conversations/{conv_id}/messages")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    # 正序
    assert [m["content"] for m in body["items"]] == ["消息0", "消息1", "消息2"]
    # 审计字段往返
    msg1 = body["items"][1]
    assert msg1["role"] == "assistant"
    assert msg1["intent"] == "faq"
    assert msg1["tool_trace"][0]["tool"] == "policy_query"
    assert msg1["compliance_status"] == "PASS"


async def test_list_messages_conversation_not_found(client: AsyncClient) -> None:
    """A05：不存在的会话 404。"""
    resp = await client.get(f"/api/v1/conversations/{uuid.uuid4()}/messages")
    assert resp.status_code == 404


# ---------- CheckpointManager ----------


async def test_checkpoint_manager_dev_uses_memory() -> None:
    """dev profile → start() 返回 InMemorySaver，close 幂等。"""
    manager = short_term_module.CheckpointManager()
    saver = await manager.start()
    assert isinstance(saver, InMemorySaver)
    assert manager.checkpointer is saver
    # 幂等 start
    assert await manager.start() is saver
    await manager.close()
    with pytest.raises(RuntimeError, match="未初始化"):
        _ = manager.checkpointer
    await manager.close()  # 幂等 close


def test_checkpoint_manager_not_started_raises() -> None:
    """未 start 直接取 checkpointer 抛错（防静默降级）。"""
    manager = short_term_module.CheckpointManager()
    with pytest.raises(RuntimeError, match="未初始化"):
        _ = manager.checkpointer
