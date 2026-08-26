"""services/db/session 会话管理测试。

通过 monkeypatch 替换全局引擎为内存 SQLite，验证 get_session 的
提交 / 回滚行为与 init_db 建表逻辑，不落盘、不依赖外部服务。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
from services.db.models import Base, Conversation


@pytest.fixture()
async def patched_engine(monkeypatch):
    """把全局引擎替换为内存 SQLite 并建表。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "_engine", engine)
    monkeypatch.setattr(session_module, "_session_factory", factory)
    yield engine
    await engine.dispose()


async def test_get_session_commits_on_success(patched_engine) -> None:
    """正常路径：请求处理完成（生成器自然耗尽）后数据提交。"""
    gen = session_module.get_session()
    session = await gen.__anext__()
    session.add(Conversation(user_id="demo-user"))
    # 驱动生成器跑完（模拟 FastAPI 依赖正常结束），触发 commit
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()

    factory = session_module.get_session_factory()
    async with factory() as check:
        rows = (await check.execute(select(Conversation))).scalars().all()
        assert len(rows) == 1
        assert rows[0].user_id == "demo-user"


async def test_get_session_rolls_back_on_error(patched_engine) -> None:
    """异常路径：请求处理抛错（athrow 注入）触发回滚，数据不入库。"""
    gen = session_module.get_session()
    session = await gen.__anext__()
    session.add(Conversation(user_id="rollback-user"))

    with pytest.raises(RuntimeError, match="boom"):
        await gen.athrow(RuntimeError("boom"))

    factory = session_module.get_session_factory()
    async with factory() as check:
        rows = (await check.execute(select(Conversation))).scalars().all()
        assert rows == []


async def test_init_db_creates_all_tables(tmp_path, monkeypatch) -> None:
    """init_db 对 SQLite 文件建表成功（用临时路径隔离，不污染 ./data）。"""
    db_file = tmp_path / "t.db"
    fake_settings = SimpleNamespace(
        database_url=f"sqlite+aiosqlite:///{db_file.as_posix()}",
        _url_for_log=lambda: f"sqlite+aiosqlite:///{db_file.as_posix()}",
    )
    monkeypatch.setattr(session_module, "settings", fake_settings)
    monkeypatch.setattr(session_module, "_engine", None)
    monkeypatch.setattr(session_module, "_session_factory", None)

    await session_module.init_db()

    engine = session_module.get_engine()
    async with engine.connect() as conn:
        created = await conn.run_sync(
            lambda sync_conn: set(
                t for t in Base.metadata.tables if t in sync_conn.dialect.get_table_names(sync_conn)
            )
        )
    assert created == {
        "conversations",
        "messages",
        "policies",
        "medical_records",
        "claim_records",
        "kb_documents",
        "human_tickets",
    }
    await session_module.dispose_engine()
    assert session_module._engine is None
