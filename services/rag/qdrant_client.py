"""Qdrant 客户端工厂：dev=local mode（本地文件零容器），prod=服务连接。

同一套客户端代码，仅部署形态不同（D001/ADR-004/D005）。
"""

from __future__ import annotations

from functools import cache

from qdrant_client import AsyncQdrantClient

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


@cache
def get_qdrant_client() -> AsyncQdrantClient:
    """获取全局异步 Qdrant 客户端（惰性单例）。

    dev：local mode（path 指向本地文件目录，无需任何服务）
    prod：连接 Qdrant 服务（url）
    """
    if settings.is_prod:
        client = AsyncQdrantClient(url=settings.qdrant_url)
        log.info("qdrant_client_created", mode="server", url=settings.qdrant_url)
    else:
        client = AsyncQdrantClient(path=settings.qdrant_local_path)
        log.info("qdrant_client_created", mode="local", path=settings.qdrant_local_path)
    return client


async def close_qdrant_client() -> None:
    """释放客户端（应用关停/测试清理）。"""
    if get_qdrant_client.cache_info().currsize:
        await get_qdrant_client().close()
        get_qdrant_client.cache_clear()


def reset_qdrant_client() -> None:
    """清空缓存但不关闭（测试中替换全局实例用）。"""
    get_qdrant_client.cache_clear()
