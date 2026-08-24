"""健康检查路由（A01）。

报告四类依赖连接状态：
- postgres：SELECT 1（dev=SQLite 文件，prod=PostgreSQL）
- qdrant：dev=local mode 路径检查；prod=get_collections 探活
- redis：dev=内存降级（skipped）；prod=PING
- llm：配置完整性检查（不真实调用 API，避免健康检查产生费用与延迟）
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter
from qdrant_client import AsyncQdrantClient
from redis import asyncio as aioredis
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import get_logger
from schemas.api import DependencyStatus, HealthResponse
from services.db.session import get_engine

router = APIRouter(tags=["health"])
log = get_logger(__name__)

# 各依赖探活超时（秒）
_CHECK_TIMEOUT = 3.0


async def _check_db() -> DependencyStatus:
    """数据库连通性：SELECT 1。"""
    try:
        engine = get_engine()
        async with asyncio.timeout(_CHECK_TIMEOUT):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return DependencyStatus(status="ok")
    except Exception as exc:
        log.warning("health_db_error", error=str(exc))
        return DependencyStatus(status="error", detail=str(exc)[:200])


async def _check_qdrant() -> DependencyStatus:
    """向量库：dev 检查 local mode 路径，prod 探活服务。"""
    if not settings.is_prod:
        path = Path(settings.qdrant_local_path)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return DependencyStatus(status="error", detail=str(exc)[:200])
        return DependencyStatus(status="ok", detail="local mode")
    try:
        client = AsyncQdrantClient(url=settings.qdrant_url, timeout=_CHECK_TIMEOUT)
        try:
            async with asyncio.timeout(_CHECK_TIMEOUT):
                await client.get_collections()
        finally:
            await client.close()
        return DependencyStatus(status="ok")
    except Exception as exc:
        log.warning("health_qdrant_error", error=str(exc))
        return DependencyStatus(status="error", detail=str(exc)[:200])


async def _check_redis() -> DependencyStatus:
    """缓存：dev 内存降级（skipped），prod PING。"""
    if not settings.is_prod:
        return DependencyStatus(status="skipped", detail="dev profile 使用内存缓存")
    try:
        client = aioredis.from_url(settings.redis_url, socket_connect_timeout=_CHECK_TIMEOUT)
        try:
            async with asyncio.timeout(_CHECK_TIMEOUT):
                await client.ping()
        finally:
            await client.aclose()
        return DependencyStatus(status="ok")
    except Exception as exc:
        log.warning("health_redis_error", error=str(exc))
        return DependencyStatus(status="error", detail=str(exc)[:200])


def _check_llm() -> DependencyStatus:
    """LLM 配置完整性：API Key 是否配置（不真实调用）。"""
    if settings.llm_api_key.strip():
        return DependencyStatus(status="ok", detail=settings.llm_model)
    return DependencyStatus(status="error", detail="LLM_API_KEY 未配置")


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """健康检查：返回整体状态与各依赖明细。"""
    db, qdrant, redis = await asyncio.gather(_check_db(), _check_qdrant(), _check_redis())
    llm = _check_llm()
    deps = {"postgres": db, "qdrant": qdrant, "redis": redis, "llm": llm}

    # 核心依赖（数据库、prod 下的向量库/缓存）故障 → error；
    # 仅 LLM 未配置 → degraded（应用可启动，对话功能不可用）
    core_fail = db.status == "error" or (
        settings.is_prod and (qdrant.status == "error" or redis.status == "error")
    )
    if core_fail:
        overall = "error"
    elif llm.status == "error":
        overall = "degraded"
    else:
        overall = "ok"

    log.info("health_checked", status=overall, deps={k: v.status for k, v in deps.items()})
    return HealthResponse(status=overall, profile=str(settings.app_profile), dependencies=deps)
