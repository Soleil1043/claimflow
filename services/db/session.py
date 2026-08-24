"""数据库会话管理（异步）。

- dev profile：SQLite(aiosqlite)，`init_db()` 直接 create_all 建表（开发便捷）
- prod profile：PostgreSQL(asyncpg)，建表走 alembic 迁移（见 alembic/）
- FastAPI 依赖注入统一用 `get_session`（app/api/dependencies.py 再导出）
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

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


def get_engine() -> AsyncEngine:
    """获取全局异步引擎（惰性单例）。"""
    global _engine
    if _engine is None:
        # SQLite 文件模式需要保证目录存在
        if settings.database_url.startswith("sqlite"):
            db_path = settings.database_url.split("///")[-1]
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_async_engine(settings.database_url, echo=False)
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
    """建表。dev 用 create_all（便捷）；prod 应走 alembic（此方法仅幂等兜底）。"""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("db_tables_created", tables=list(Base.metadata.tables))


async def dispose_engine() -> None:
    """释放全局引擎（应用关停/测试清理用）。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
