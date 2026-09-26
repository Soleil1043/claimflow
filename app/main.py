"""FastAPI 应用入口。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from prometheus_client import generate_latest

from app.api.v1 import cases, health, interventions, memory, support
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from services.db.session import dispose_engine, init_db
from services.memory.short_term import get_checkpoint_manager

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：初始化日志 / 数据库 / checkpointer / 主图，关停时逆序释放。"""
    configure_logging()

    checkpointer = await get_checkpoint_manager().start()

    # BUG-003 配套：prod 长期记忆 Store 启动期预建（开池 + 建表）。不能懒加载到首个
    # 请求——store 迁移含 CREATE INDEX CONCURRENTLY，会等请求自身已开的事务结束，
    # 同请求内自锁（实测挂死 3 分钟）。失败仅告警不阻断启动（记忆是旁路路径）。
    if settings.is_prod and settings.memory_enabled:
        from services.memory.long_term import _ensure_pg_setup, get_memory_store

        try:
            await _ensure_pg_setup(get_memory_store())
        except Exception as exc:  # noqa: BLE001
            log.warning("memory_store_setup_failed", error=str(exc)[:200])

    # dev 直接建表；prod 由 alembic 迁移管理，不自动建表
    if not settings.is_prod:
        await init_db()

    # T048：BGE-M3 启动预热（在线程池加载，不阻塞事件循环）——冷加载约 10-20s，
    # 若泄漏到首个工具调用的守卫超时窗口会连坐引爆（T048 评测实测）
    from services.rag.embedder import preload_embedding_model

    await asyncio.to_thread(preload_embedding_model)

    # Phase 8 T080/T093：核赔案件主图
    from workflows.case_graph import create_default_case_graph

    app.state.case_graph: Any = create_default_case_graph(checkpointer=checkpointer)

    # T103 案件交付队列（D044 混合方案）：派发器 + 常驻消费者循环（background 档）
    from services.case_jobs import JobLoop, make_case_dispatcher
    from services.case_store import get_default_recorder

    app.state.case_dispatcher: Any = make_case_dispatcher(
        app.state.case_graph, get_default_recorder()
    )
    job_loop: Any = None
    if settings.case_jobs_execution == "background":
        job_loop = JobLoop(app.state.case_graph, get_default_recorder())
        await job_loop.start()  # 多实例安全（T155 租约）：无全局回收，孤儿由租约到期接管
    log.info("app_started", profile=str(settings.app_profile),
             case_jobs=str(settings.case_jobs_execution))
    yield

    from services.cache import get_tool_cache

    if job_loop is not None:
        await job_loop.stop()  # 排水必须最先：在飞 ainvoke 依赖存活的 saver/engine
    await (await get_tool_cache()).close()
    await get_checkpoint_manager().close()
    await dispose_engine()
    log.info("app_stopped")


app = FastAPI(
    title="claimflow",
    description="保险理赔智能核赔平台（多险种）",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(interventions.router)
app.include_router(cases.router)
app.include_router(support.router)
app.include_router(memory.router)

# T039：OTel 追踪——必须在模块级（应用启动前）instrument：Starlette 的 middleware
# 栈在 lifespan 开始前已构建，lifespan 内 add_middleware 无效（server span 缺失的实测坑）。
# OTEL_ENABLED=false 时为 no-op；exporter 惰性连接，无 collector 也不阻塞启动。
from services.observability.tracing import setup_tracing  # noqa: E402

setup_tracing(app)


@app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
async def prometheus_metrics() -> PlainTextResponse:
    """Prometheus 抓取端点（T024）：工具 / LLM / 业务三类指标。"""
    return PlainTextResponse(
        generate_latest(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
