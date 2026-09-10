"""long_term Store 存储层单测（T113 收敛后存活面）。

业务路径（申请人记忆写读闭环、幂等、用户隔离）由 tests/memory/test_case_memory.py 覆盖；
本文件聚焦 Store 装配回归——BUG-003（prod conn 必须是连接池，不能传 DSN 字符串）
与 _ensure_pg_setup 的开池/建表幂等。
"""

from __future__ import annotations

from typing import Any

import pytest

import services.memory.long_term as lt

# ===== BUG-003：prod Store 装配回归（conn 必须是连接池，不能再传 DSN 字符串） =====


class _FakeAsyncPool:
    """psycopg_pool.AsyncConnectionPool 桩：记录构造参数与 open 调用。"""

    def __init__(self, conninfo: str, **kwargs: Any) -> None:
        self.conninfo = conninfo
        self.kwargs = kwargs
        self.open_count = 0

    async def open(self) -> None:
        self.open_count += 1


class _FakeAsyncStore:
    """AsyncPostgresStore 桩：记录 conn 参数与 setup 调用。"""

    last: _FakeAsyncStore | None = None

    def __init__(self, *, conn: Any, index: dict | None = None) -> None:
        self.conn = conn
        self.index = index
        self.setup_count = 0
        _FakeAsyncStore.last = self

    async def setup(self) -> None:
        self.setup_count += 1


def test_prod_store_builds_connection_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """prod 分支必须传 AsyncConnectionPool（官方 from_conn_string 同款配方），不得传 str。"""
    from app.core.config import Profile

    monkeypatch.setattr(lt.settings, "app_profile", Profile.PROD)
    monkeypatch.setattr(
        "langgraph.store.postgres.AsyncPostgresStore", _FakeAsyncStore
    )
    monkeypatch.setattr("psycopg_pool.AsyncConnectionPool", _FakeAsyncPool)
    lt.reset_memory_store()
    try:
        store = lt.get_memory_store()
        assert isinstance(store, _FakeAsyncStore)
        # 核心：conn 是池实例而非 DSN 字符串（回归点：Invalid connection type）
        assert isinstance(store.conn, _FakeAsyncPool)
        assert not isinstance(store.conn, str)
        pool = store.conn
        assert pool.kwargs.get("open") is False, "池必须延迟到异步上下文再打开"
        row_kwargs = pool.kwargs.get("kwargs") or {}
        assert row_kwargs.get("autocommit") is True
        assert row_kwargs.get("prepare_threshold") == 0
        assert "row_factory" in row_kwargs, "缺 dict_row 会导致行解析错位"
        assert (store.index or {}).get("dims") == lt.EMBEDDING_DIM
    finally:
        lt.reset_memory_store()


async def test_ensure_pg_setup_opens_pool_and_creates_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ensure_pg_setup 首次调用：开池 + setup 建表；二次调用幂等不重复。"""
    from app.core.config import Profile

    monkeypatch.setattr(lt.settings, "app_profile", Profile.PROD)
    pool = _FakeAsyncPool("postgresql://x")
    store = _FakeAsyncStore(conn=pool)
    monkeypatch.setattr(lt, "_pg_pool", pool)
    monkeypatch.setattr(lt, "_pg_setup_done", False)
    await lt._ensure_pg_setup(store)
    assert pool.open_count == 1
    assert store.setup_count == 1
    # 幂等：重复调用不再开池/建表
    await lt._ensure_pg_setup(store)
    assert pool.open_count == 1
    assert store.setup_count == 1
