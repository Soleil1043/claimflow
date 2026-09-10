"""记忆 Store 存储层（LangGraph 官方 Store 体系，T048/D021/ADR-007）。

- dev：InMemoryStore（进程内，零依赖）
- prod：AsyncPostgresStore（langgraph.store.postgres，复用 psycopg 连接）
- 向量检索：Store 内建 index（IndexConfig：dims=1024 / embed=BGE-M3 / fields=[embed_text]），
  namespace 按 (user_id,) 隔离
- 业务写入方：申请人核赔档案（services/memory/case_memory.py，确定性渲染）

本模块只承载 Store 单例装配与原始条目检索门面（search_store_items，调用方自行按
value.kind 过滤记忆种类）；业务语义与幂等口径在 case_memory。
"""

from __future__ import annotations

import inspect
from typing import Any

from langgraph.store.base import BaseStore

from app.core.config import settings
from app.core.logging import get_logger
from services.rag.embedder import EMBEDDING_DIM, embed_texts

log = get_logger(__name__)

_MEMORY_NAMESPACE = "memory"

# ===== Store 单例装配 =====

_memory_store: BaseStore | None = None
_pg_setup_done = False
# prod 连接池（typing.Any：psycopg_pool 仅 prod 路径导入，dev 不引入该依赖类型）
_pg_pool: Any | None = None


def _embed_for_store(texts: list[str]) -> list[list[float]]:
    """Store index 嵌入函数（BGE-M3 同源，1024 维）。"""
    return embed_texts(texts)


def get_memory_store() -> BaseStore:
    """记忆 Store 单例：dev=InMemoryStore / prod=AsyncPostgresStore（均带向量 index）。"""
    global _memory_store, _pg_pool
    if _memory_store is None:
        index = {
            "dims": EMBEDDING_DIM,
            "embed": _embed_for_store,
            "fields": ["embed_text"],
        }
        if settings.app_profile.value == "prod":
            # BUG-003：conn 参数只接受 AsyncConnection / AsyncConnectionPool，
            # 传 DSN 字符串会在首次 put/search 抛 Invalid connection type——
            # 记忆是旁路路径，异常被吞成长期静默失效。这里按官方 from_conn_string
            # 的同款配方自建池（autocommit / prepare_threshold=0 / dict_row），
            # 池 open=False 延迟到 _ensure_pg_setup（异步上下文）再打开。
            from langgraph.store.postgres import AsyncPostgresStore
            from psycopg.rows import dict_row
            from psycopg_pool import AsyncConnectionPool

            dsn = (
                f"postgresql://{settings.postgres_user}:{settings.postgres_password}"
                f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
            )
            _pg_pool = AsyncConnectionPool(
                dsn,
                min_size=1,
                open=False,
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                    "row_factory": dict_row,
                },
            )
            _memory_store = AsyncPostgresStore(conn=_pg_pool, index=index)  # type: ignore[call-arg]
            log.info("memory_store_initialized", backend="AsyncPostgresStore")
        else:
            from langgraph.store.memory import InMemoryStore

            _memory_store = InMemoryStore(index=index)  # type: ignore[call-arg]
            log.info("memory_store_initialized", backend="InMemoryStore", dim=EMBEDDING_DIM)
    return _memory_store


def reset_memory_store() -> None:
    """重置 Store 单例（测试用：换嵌入桩后重建）。"""
    global _memory_store, _pg_setup_done, _pg_pool
    _memory_store = None
    _pg_setup_done = False
    _pg_pool = None


async def _ensure_pg_setup(store: BaseStore) -> None:
    """prod AsyncPostgresStore 首用时开池 + 建表（幂等，一次）。"""
    global _pg_setup_done
    if _pg_setup_done or settings.app_profile.value != "prod":
        return
    if _pg_pool is not None:
        await _pg_pool.open()
    setup = getattr(store, "setup", None)
    if setup is not None:
        result = setup()
        if inspect.isawaitable(result):
            await result
    _pg_setup_done = True


async def search_store_items(user_id: str, query: str, limit: int) -> list[Any]:
    """Store 原始条目检索（T100 门面：调用方自行按 value.kind 过滤记忆种类）。

    BUG-003 口径：asearch 优先，AsyncPostgresStore 只能走异步接口。
    异常向上抛——调用方决定旁路语义。
    """
    store = get_memory_store()
    await _ensure_pg_setup(store)
    asearch = getattr(store, "asearch", None)
    if asearch is not None:
        return list(await asearch((_MEMORY_NAMESPACE, user_id), query=query, limit=limit))
    result = store.search((_MEMORY_NAMESPACE, user_id), query=query, limit=limit)
    return list(await result) if inspect.isawaitable(result) else list(result)
