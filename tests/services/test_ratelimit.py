"""门户限流单元测试（T172，D078）：内存令牌桶语义 + 门面 0 配额口径。

Redis Lua 后端不在此测（需真实 Redis；脚本语义与内存桶对齐，prod 冒烟覆盖）。
"""

from __future__ import annotations

import asyncio

from services.ratelimit import MemoryTokenBucket, PortalRateLimiter


class TestMemoryTokenBucket:
    async def test_within_capacity_passes(self):
        bucket = MemoryTokenBucket()
        for _ in range(5):
            assert await bucket.check("k", capacity=5, refill_per_sec=5 / 60) is None

    async def test_exhausted_returns_wait(self):
        bucket = MemoryTokenBucket()
        for _ in range(3):
            assert await bucket.check("k", capacity=3, refill_per_sec=3 / 60) is None
        wait = await bucket.check("k", capacity=3, refill_per_sec=3 / 60)
        assert wait is not None and 0 < wait <= 20  # 1/0.05 = 20s 补满一枚

    async def test_refill_over_time(self):
        """耗尽后等待 ≈ 一枚令牌的回填时长（60/配额 秒）。"""
        bucket = MemoryTokenBucket()
        for _ in range(2):
            await bucket.check("k", capacity=2, refill_per_sec=2.0)  # 2 枚/秒，快速回填
        wait = await bucket.check("k", capacity=2, refill_per_sec=2.0)
        assert wait is not None and wait > 0
        await asyncio.sleep(0.6)  # 回填 >1 枚
        assert await bucket.check("k", capacity=2, refill_per_sec=2.0) is None

    async def test_tokens_capped_at_capacity(self):
        """闲置不超容量：满桶等待一个周期后仍只放行 capacity 枚。"""
        bucket = MemoryTokenBucket()
        await asyncio.sleep(0.05)  # 闲置（桶初始即满，无额外补充）
        for _ in range(2):
            assert await bucket.check("k", capacity=2, refill_per_sec=1 / 60) is None
        assert await bucket.check("k", capacity=2, refill_per_sec=1 / 60) is not None

    async def test_keys_isolated(self):
        bucket = MemoryTokenBucket()
        for _ in range(2):
            await bucket.check("ip-a", capacity=2, refill_per_sec=1 / 60)
        assert await bucket.check("ip-a", capacity=2, refill_per_sec=1 / 60) is not None
        assert await bucket.check("ip-b", capacity=2, refill_per_sec=1 / 60) is None


class TestPortalRateLimiterFacade:
    async def test_zero_quota_disables(self):
        limiter = PortalRateLimiter(MemoryTokenBucket())
        for _ in range(100):
            assert await limiter.acquire("cases", "1.2.3.4", per_minute=0) is None

    async def test_scope_and_ip_keyed(self):
        limiter = PortalRateLimiter(MemoryTokenBucket())
        for _ in range(2):
            assert await limiter.acquire("cases", "1.2.3.4", per_minute=2) is None
        assert await limiter.acquire("cases", "1.2.3.4", per_minute=2) is not None
        # 同 IP 不同域 / 不同 IP 同域：各自独立配额
        assert await limiter.acquire("support_messages", "1.2.3.4", per_minute=2) is None
        assert await limiter.acquire("cases", "5.6.7.8", per_minute=2) is None
