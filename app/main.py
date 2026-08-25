"""FastAPI 应用入口。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from prometheus_client import generate_latest

from app.api.v1 import conversations, health
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from services.db.session import dispose_engine, init_db
from services.memory.short_term import get_checkpoint_manager
from tools.executor import ToolExecutor
from tools.registry import get_default_registry
from workflows.main_graph import build_main_graph

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：初始化日志 / 数据库 / checkpointer / 主图，关停时逆序释放。"""
    configure_logging()

    import tools.claim  # noqa: F401 注册理赔工具
    import tools.compliance  # noqa: F401 注册合规工具
    import tools.medical  # noqa: F401 注册医疗工具

    registry = get_default_registry()
    checkpointer = await get_checkpoint_manager().start()

    # dev 直接建表；prod 由 alembic 迁移管理，不自动建表
    if not settings.is_prod:
        await init_db()

    app.state.graph: Any = build_main_graph(
        executor=ToolExecutor(registry),
        checkpointer=checkpointer,
    )
    log.info("app_started", profile=str(settings.app_profile), tools=registry.list_names())
    yield

    await get_checkpoint_manager().close()
    await dispose_engine()
    log.info("app_stopped")


app = FastAPI(
    title="claimflow",
    description="多智能体保险理赔对话系统",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(conversations.router)


@app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
async def prometheus_metrics() -> PlainTextResponse:
    """Prometheus 抓取端点（T024）：工具 / LLM / 业务三类指标。"""
    return PlainTextResponse(
        generate_latest(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
