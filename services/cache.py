"""工具结果缓存服务（T028，D005 profile 降级）。

- prod：Redis（redis.asyncio），跨进程共享，LRU 淘汰交给 Redis 内存策略
- dev：进程内 TTL 字典（零容器依赖，语义与 Redis 对齐）

key 规则：claimflow:toolcache:{tool}:{入参指纹}（sha256 of canonical json）。
value：ToolOutput 序列化 JSON。TTL 由 settings.tool_cache_ttl_seconds 控制。
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Protocol

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


class ToolCacheBackend(Protocol):
    """缓存后端协议：get / set / close。"""

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...
    async def close(self) -> None: ...


class RedisToolCache:
    """Redis 后端（prod）。"""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def get(self, key: str) -> str | None:
        return await self._redis.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await self._redis.set(key, value, ex=ttl_seconds)

    async def close(self) -> None:
        await self._redis.aclose()


class MemoryToolCache:
    """内存 TTL 后端（dev 降级）：与 Redis 语义对齐的字典实现。"""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float]] = {}  # key → (value, expire_at)

    async def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expire_at = entry
        if time.monotonic() > expire_at:
            del self._store[key]
            return None
        return value

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._store[key] = (value, time.monotonic() + ttl_seconds)

    async def close(self) -> None:
        self._store.clear()


class _NoopBackend:
    """禁用态后端：永不命中。"""

    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        return None

    async def close(self) -> None:
        return None


class ToolResultCache:
    """工具结果缓存门面：key 生成 + 序列化 + 后端路由（prod Redis / dev 内存）。"""

    _PREFIX = "claimflow:toolcache"

    def __init__(self, backend: ToolCacheBackend) -> None:
        self._backend = backend

    @classmethod
    def make_key(cls, tool_name: str, input_data: dict[str, Any]) -> str:
        """入参指纹 key：canonical json（排序键）→ sha256 前 16 位。"""
        canonical = json.dumps(input_data, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        return f"{cls._PREFIX}:{tool_name}:{digest}"

    async def get(self, tool_name: str, input_data: dict[str, Any]) -> dict[str, Any] | None:
        """命中返回 ToolOutput 的 dict 形态；未命中返回 None。"""
        raw = await self._backend.get(self.make_key(tool_name, input_data))
        if raw is None:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("tool_cache_corrupted", tool=tool_name)
            return None
        return parsed if isinstance(parsed, dict) else None

    async def set(self, tool_name: str, input_data: dict[str, Any], output: dict[str, Any]) -> None:
        await self._backend.set(
            self.make_key(tool_name, input_data),
            json.dumps(output, ensure_ascii=False, default=str),
            settings.tool_cache_ttl_seconds,
        )

    async def close(self) -> None:
        await self._backend.close()


_cache: ToolResultCache | None = None


async def get_tool_cache() -> ToolResultCache:
    """获取全局缓存实例（惰性初始化；enabled=False 时返回无操作后端）。"""
    global _cache
    if _cache is not None:
        return _cache

    if not settings.tool_cache_enabled:
        _cache = ToolResultCache(_NoopBackend())
        log.info("tool_cache_disabled")
    elif settings.is_prod:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        _cache = ToolResultCache(RedisToolCache(client))
        log.info("tool_cache_initialized", backend="redis", url=settings.redis_url)
    else:
        _cache = ToolResultCache(MemoryToolCache())
        log.info("tool_cache_initialized", backend="memory", ttl_s=settings.tool_cache_ttl_seconds)
    return _cache


def reset_tool_cache() -> None:
    """重置单例（测试用）。"""
    global _cache
    _cache = None


def cached_tools() -> set[str]:
    """幂等工具白名单（配置驱动）。"""
    return {t.strip() for t in settings.tool_cache_tools.split(",") if t.strip()}
