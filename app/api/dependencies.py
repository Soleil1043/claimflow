"""依赖注入：路由层统一从这里获取会话、配置与主图。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from services.db.session import get_session


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """请求级数据库会话（透传 services.db.session.get_session）。"""
    async for session in get_session():
        yield session


def get_app_settings() -> Settings:
    """全局配置单例。"""
    return settings


def get_app_graph(request: Request) -> Any:
    """应用级主图（lifespan 中初始化到 app.state.graph）。"""
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        msg = "主图未初始化（lifespan 未启动？）"
        raise RuntimeError(msg)
    return graph
