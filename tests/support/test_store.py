"""客服会话域 store 测试（T132）：状态机 + 消息读写 + replay 窗口 + 迁移升降。"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
from services.db.models import Base
from services.support import store


@pytest.fixture()
async def support_db(tmp_path):
    """独立文件库 + swap_engine（T109 公开 seam），建全量表。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'support_store_test.db').as_posix()}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module.swap_engine(engine, factory)
    yield factory
    await engine.dispose()


async def test_create_and_get_conversation(support_db) -> None:
    """新建会话：uuid hex 主键、初始 ai、时间戳落库。"""
    conv = await store.create_conversation()
    assert conv.status == store.CONV_AI
    assert len(conv.id) == 32

    loaded = await store.get_conversation(conv.id)
    assert loaded is not None
    assert loaded.status == store.CONV_AI
    assert loaded.created_at is not None

    assert await store.get_conversation("nonexistent") is None


async def test_append_message_ordering(support_db) -> None:
    """消息 append-only，按写入序（id 升序）读取。"""
    conv = await store.create_conversation()
    await store.append_message(conv.id, role=store.ROLE_USER, content="您好")
    await store.append_message(conv.id, role=store.ROLE_ASSISTANT, content="请问有什么可以帮您")

    msgs = await store.list_messages(conv.id)
    assert [(m.role, m.content) for m in msgs] == [
        (store.ROLE_USER, "您好"),
        (store.ROLE_ASSISTANT, "请问有什么可以帮您"),
    ]
    assert all(m.created_at is not None for m in msgs)


async def test_recent_messages_window(support_db) -> None:
    """replay 窗口：取最近 N 条，仍按写入序升序返回（Agent 记忆截断用）。"""
    conv = await store.create_conversation()
    for i in range(5):
        await store.append_message(conv.id, role=store.ROLE_USER, content=f"m{i}")

    recent = await store.recent_messages(conv.id, limit=3)
    assert [m.content for m in recent] == ["m2", "m3", "m4"]


async def test_escalate_flow_ai_muted(support_db) -> None:
    """转人工：ai → escalated，原因留痕，AI 停答、用户/坐席可写。"""
    conv = await store.create_conversation()
    await store.escalate_conversation(conv.id, reason="用户要求人工")

    loaded = await store.get_conversation(conv.id)
    assert loaded.status == store.CONV_ESCALATED
    assert loaded.escalated_at is not None
    assert loaded.escalated_reason == "用户要求人工"

    # escalated 后 AI 停答
    with pytest.raises(store.SupportStateError):
        await store.append_message(conv.id, role=store.ROLE_ASSISTANT, content="x")
    # 用户与人工坐席仍可写
    await store.append_message(conv.id, role=store.ROLE_USER, content="好的")
    await store.append_message(conv.id, role=store.ROLE_AGENT, content="您好，我是人工坐席")


async def test_close_flow_terminal(support_db) -> None:
    """关闭：escalated → closed 终态，任何写入与流转均拒绝。"""
    conv = await store.create_conversation()
    await store.escalate_conversation(conv.id)
    await store.close_conversation(conv.id)

    loaded = await store.get_conversation(conv.id)
    assert loaded.status == store.CONV_CLOSED
    assert loaded.closed_at is not None

    with pytest.raises(store.SupportStateError):
        await store.append_message(conv.id, role=store.ROLE_USER, content="x")
    with pytest.raises(store.SupportStateError):
        await store.append_message(conv.id, role=store.ROLE_AGENT, content="x")
    # closed 不可逆
    with pytest.raises(store.SupportStateError):
        await store.escalate_conversation(conv.id)
    with pytest.raises(store.SupportStateError):
        await store.close_conversation(conv.id)


async def test_direct_close_from_ai(support_db) -> None:
    """ai → closed 直接关闭合法（用户未转人工即结束对话）。"""
    conv = await store.create_conversation()
    await store.close_conversation(conv.id)
    assert (await store.get_conversation(conv.id)).status == store.CONV_CLOSED


async def test_double_escalate_rejected(support_db) -> None:
    """escalated → escalated 非法（重复转人工拒绝，幂等语义由调用方处理）。"""
    conv = await store.create_conversation()
    await store.escalate_conversation(conv.id)
    with pytest.raises(store.SupportStateError):
        await store.escalate_conversation(conv.id)


async def test_missing_conversation_raises(support_db) -> None:
    """会话不存在：LookupError（正常业务信号，非系统异常）。"""
    with pytest.raises(LookupError):
        await store.append_message("nope", role=store.ROLE_USER, content="x")
    with pytest.raises(LookupError):
        await store.escalate_conversation("nope")
    with pytest.raises(LookupError):
        await store.close_conversation("nope")


def test_migration_up_down(tmp_path, monkeypatch) -> None:
    """客服迁移单测可升可降：空库 stamp 到上一版 → upgrade 建两表 → downgrade 删（T132 验收）。

    不跑全链：旧迁移（如 b5f9c3d7e2a4 加唯一约束）在 SQLite 方言本就不可执行
    （dev 走 create_all、prod 走 PostgreSQL），单测本迁移的 DDL 升降即可。
    """
    import app.core.config as config_module

    db_path = (tmp_path / "mig_test.db").as_posix()
    # env.py 从 settings.database_url 取连接串——替换为临时库（类级 property 替换，测后还原）
    monkeypatch.setattr(
        type(config_module.settings),
        "database_url",
        property(lambda self: f"sqlite+aiosqlite:///{db_path}"),
    )

    from alembic.config import Config as AlembicConfig

    from alembic import command

    root = Path(__file__).resolve().parents[2]
    acfg = AlembicConfig()
    acfg.set_main_option("script_location", str(root / "alembic"))

    # 空库直接 stamp 到上一版（b7d2e6f9a3c1 只依赖 support 两表自身，无前置表）
    command.stamp(acfg, "d9a5c1e8f3b7")
    command.upgrade(acfg, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert {"support_conversations", "support_messages"} <= tables
    engine.dispose()

    command.downgrade(acfg, "-1")
    engine2 = create_engine(f"sqlite:///{db_path}")
    tables_after = set(inspect(engine2).get_table_names())
    assert "support_conversations" not in tables_after
    assert "support_messages" not in tables_after
    engine2.dispose()
