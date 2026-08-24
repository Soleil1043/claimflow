"""FastAPI 应用入口。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1 import health
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from services.db.session import dispose_engine, init_db

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动时初始化日志与数据库，关停时释放连接。"""
    configure_logging()
    # dev 直接 create_all 建表；prod 由 alembic 迁移管理，不自动建表
    if not settings.is_prod:
        await init_db()
    log.info("app_started", profile=str(settings.app_profile))
    yield
    await dispose_engine()
    log.info("app_stopped")


app = FastAPI(
    title="claim-agent",
    description="多智能体保险理赔对话系统",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router)
