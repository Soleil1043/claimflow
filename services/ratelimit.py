"""门户限流服务（T172，D078：自研令牌桶，不引第三方依赖）。

- prod：Redis（redis.asyncio + Lua 原子扣减）——多实例共享桶，时间源取 Redis TIME
  避免应用实例间时钟漂移
- dev：进程内内存桶（零容器依赖，语义与 Redis 对齐；单事件循环内无 await 竞态，
  免锁）

语义：令牌桶——容量 = 每分钟配额（允许瞬时突发至容量），按 per_min/60 每秒连续
回填。取令牌失败返回需等待秒数（调用方转 429 + Retry-After），成功返回 None。
键：claimflow:ratelimit:{scope}:{client_ip}。

配置（app/core/config.py）：
- rate_limit_cases_per_min / rate_limit_support_per_min：0 = 该域不限流
  （负载测试在服务器侧置 0 后压测）
- rate_limit_trust_forwarded：是否信任 X-Forwarded-For 首跳为真实客户端 IP
  （默认 False——直连场景下该头可伪造轮转键绕过；仅反向代理部署时开启）
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from app.core.config import settings
from app.core.logging import get_logger
from services.observability.metrics import RATE_LIMIT_REJECTED

log = get_logger(__name__)

# Redis 令牌桶原子扣减：HMGET 旧态 → 按时间差回填 → 扣 1 或算等待 → 回写 + TTL。
# 时间源用 Redis TIME（脚本内非确定命令，Redis 5+ effects 复制下安全），
# 返回等待秒数字符串（"0" = 通过）
_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(redis.call('TIME')[1]) + tonumber(redis.call('TIME')[2]) / 1e6
local state = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(state[1]) or capacity
local ts = tonumber(state[2]) or now
tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate)
local wait = 0
if tokens >= 1 then
  tokens = tokens - 1
else
  wait = (1 - tokens) / rate
end
redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, math.max(60, math.ceil(capacity / rate) * 2))
return tostring(wait)
"""


class RateLimitBackend(Protocol):
    """限流后端协议：check 取一枚令牌，None=通过，float=需等待秒数。"""

    async def check(
        self, key: str, capacity: int, refill_per_sec: float
    ) -> float | None: ...


class RedisTokenBucket:
    """Redis 后端（prod）：Lua 脚本原子完成回填+扣减，多实例共享同一桶。"""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def check(
        self, key: str, capacity: int, refill_per_sec: float
    ) -> float | None:
        wait = float(
            await self._redis.eval(_BUCKET_LUA, 1, key, capacity, refill_per_sec)
        )
        return None if wait <= 0 else wait

    async def close(self) -> None:
        await self._redis.aclose()


class MemoryTokenBucket:
    """内存桶后端（dev 降级）：每 key 一桶，monotonic 惰性回填。

    check 全程无 await（事件循环内原子），免锁。桶字典容量护栏：超上限整体清空
    ——仅 dev 后端可接受（prod 走 Redis），防长时运行键膨胀。
    """

    _MAX_KEYS = 10_000

    def __init__(self) -> None:
        self._buckets: dict[str, tuple[float, float]] = {}  # key → (tokens, 上次回填)

    async def check(
        self, key: str, capacity: int, refill_per_sec: float
    ) -> float | None:
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (float(capacity), now))
        tokens = min(capacity, tokens + max(0.0, now - last) * refill_per_sec)
        if tokens >= 1.0:
            self._buckets[key] = (tokens - 1.0, now)
            if len(self._buckets) > self._MAX_KEYS:
                self._buckets.clear()
            return None
        self._buckets[key] = (tokens, now)
        return (1.0 - tokens) / refill_per_sec

    async def close(self) -> None:
        self._buckets.clear()


class PortalRateLimiter:
    """门面：scope 维度配额 + key 拼装 + 后端路由（prod Redis / dev 内存）。"""

    _PREFIX = "claimflow:ratelimit"

    def __init__(self, backend: RateLimitBackend) -> None:
        self._backend = backend

    async def acquire(self, scope: str, client_ip: str, per_minute: int) -> float | None:
        """取一枚令牌。per_minute<=0 = 该域不限流（负载测试口径）。"""
        if per_minute <= 0:
            return None
        return await self._backend.check(
            f"{self._PREFIX}:{scope}:{client_ip}",
            capacity=per_minute,
            refill_per_sec=per_minute / 60.0,
        )

    async def close(self) -> None:
        await self._backend.close()


_limiter: PortalRateLimiter | None = None


async def get_rate_limiter() -> PortalRateLimiter:
    """全局限流器单例（惰性初始化，prod Redis / dev 内存）。"""
    global _limiter
    if _limiter is not None:
        return _limiter
    if settings.is_prod:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        _limiter = PortalRateLimiter(RedisTokenBucket(client))
        log.info("rate_limiter_initialized", backend="redis", url=settings.redis_url)
    else:
        _limiter = PortalRateLimiter(MemoryTokenBucket())
        log.info("rate_limiter_initialized", backend="memory")
    return _limiter


def reset_rate_limiter() -> None:
    """重置单例（测试用）。"""
    global _limiter
    _limiter = None


def client_ip(request: Any) -> str:
    """客户端 IP：默认 socket 对端；trust_forwarded 开启时取 X-Forwarded-For 首跳。"""
    if settings.rate_limit_trust_forwarded:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def note_rejection(scope: str) -> None:
    """限流拒绝计数（Prometheus）。"""
    RATE_LIMIT_REJECTED.labels(scope=scope).inc()
