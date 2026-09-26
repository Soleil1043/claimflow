"""数据库会话管理（异步）。

- dev profile：SQLite(aiosqlite)，`init_db()` 直接 create_all 建表（开发便捷）
- prod profile：PostgreSQL(asyncpg)，建表走 alembic 迁移（见 alembic/）
- FastAPI 依赖注入统一用 `get_session`（app/api/dependencies.py 再导出）
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.logging import get_logger
from services.db.models import Base

log = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def swap_engine(engine: AsyncEngine, factory: async_sessionmaker[AsyncSession]) -> None:
    """替换全局引擎与会话工厂（T109 公开 seam：测试/评测换库唯一入口）。

    此前四处调用方直接赋值私有全局 _engine/_session_factory——现收敛为一个
    公开 interface。调用方自行管理替换对象的生命周期（pytest monkeypatch /
    评测退出 dispose）。
    """
    global _engine, _session_factory
    _engine = engine
    _session_factory = factory


def get_engine() -> AsyncEngine:
    """获取全局异步引擎（惰性单例）。"""
    global _engine
    if _engine is None:
        # SQLite 文件模式需要保证目录存在
        if settings.database_url.startswith("sqlite"):
            db_path = settings.database_url.split("///")[-1]
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # 池容量（T150 负载测试实锤）：SQLAlchemy 默认 pool_size=5 + max_overflow=10
        # = 15 并发连接上限——门户 N 案并发轮询（每案一路 GET）+ 交付队列消费并发
        # 超过 15 时，后续请求等池 30s 后 TimeoutError 500（50 案轮询实测 36 案 500）。
        # 扩到 20+30：覆盖门户全量并发轮询；asyncpg 同配置适用。
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_size=20,
            max_overflow=30,
        )
        log.info("db_engine_created", url=settings._url_for_log())
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取会话工厂（惰性单例）。"""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：请求级会话，自动提交/回滚/关闭。"""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """建表 + 存量库补列。dev 用 create_all（便捷）；prod 应走 alembic（此方法仅幂等兜底）。

    create_all 只建新表不动旧表（T155 实锤：存量 dev 库缺 case_jobs 租约列，
    JobLoop 认领即 no such column）——建表后对已存在表补齐 metadata 里新增的
    可空列（ALTER ADD COLUMN，SQLite/PG 均支持无默认值可空列），旧库平滑升级。
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
    log.info("db_tables_created", tables=list(Base.metadata.tables))


def _add_missing_columns(sync_conn: Any) -> None:
    """对已存在的表补 metadata 中缺失的可空列（幂等；仅 dev 便捷路径）。"""

    from sqlalchemy import inspect

    inspector = inspect(sync_conn)
    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            continue  # create_all 刚建的新表
        existing = {col["name"] for col in inspector.get_columns(table_name)}
        missing = [
            col for name, col in table.columns.items()
            if name not in existing and col.nullable and col.server_default is None
        ]
        for col in missing:
            col_type = col.type.compile(sync_conn.dialect)
            sync_conn.execute(
                text(f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type}")
            )
            log.info("db_column_backfilled", table=table_name, column=col.name)


async def dispose_engine() -> None:
    """释放全局引擎（应用关停/测试清理用）。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
