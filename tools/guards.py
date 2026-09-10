"""工具守卫层（T044，D021/D025）：熔断 / 缓存 / 超时下沉到工具对象。

D021 仅保留的两项自研在此落地：
- 熔断器：LangGraph/LangChain 无对应物（进程级守卫，不进图拓扑——作用域与
  thread 级图状态错配）
- 工具结果缓存白名单（T028）：框架无工具级缓存标准

其余保障用官方机制：重试 = Runnable `.with_retry()`、降级 = `.with_fallbacks()`
（均在 factory 装配，包在守卫内层）；超时 = stdlib `asyncio.timeout`。

装配顺序（外→内）：GuardedTool（缓存 → 熔断 → 超时）→ with_retry(原工具)。
守卫随工具对象走：agent 循环内（ToolNode）与循环外（节点直调、合规节点）
所有调用点统一生效。超时为总预算（含重试）。
"""

from __future__ import annotations

import asyncio
import time
from enum import StrEnum
from typing import Any

from pydantic import PrivateAttr

from app.core.exceptions import ToolExecutionError
from app.core.logging import get_logger
from services.observability import metrics
from tools.base import ClaimflowTool

log = get_logger(__name__)


class _BreakerState(StrEnum):
    """熔断器三态。"""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """单工具熔断器：连续失败达阈值 → open 一个冷却期 → half-open 放行探测。"""

    def __init__(self, failure_threshold: int, cooldown_seconds: float) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.state = _BreakerState.CLOSED
        self.consecutive_failures = 0
        self.opened_at: float | None = None

    def allow_call(self) -> bool:
        """是否放行本次调用。"""
        if self.state == _BreakerState.CLOSED:
            return True
        if self.state == _BreakerState.OPEN:
            assert self.opened_at is not None
            if time.monotonic() - self.opened_at >= self.cooldown_seconds:
                self.state = _BreakerState.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN：放行探测请求

    def record_success(self) -> None:
        """调用成功：关闭熔断器并清零计数。"""
        self.consecutive_failures = 0
        if self.state != _BreakerState.CLOSED:
            self.state = _BreakerState.CLOSED
            self.opened_at = None

    def record_failure(self) -> None:
        """调用失败：累计计数，达到阈值则打开熔断器。"""
        self.consecutive_failures += 1
        if (
            self.state == _BreakerState.HALF_OPEN
            or self.consecutive_failures >= self.failure_threshold
        ):
            self.state = _BreakerState.OPEN
            self.opened_at = time.monotonic()
            log.warning(
                "circuit_breaker_opened",
                consecutive_failures=self.consecutive_failures,
                cooldown_s=self.cooldown_seconds,
            )


class GuardedTool(ClaimflowTool):
    """守卫包装：缓存白名单 → 熔断 → 超时，包裹内层 Runnable（已含官方重试）。

    工厂（tools/factory.py）负责装配；本类不自建内层。守卫命中缓存不算真实执行，
    不进熔断/耗时统计。
    """

    _inner: Any = PrivateAttr()  # Runnable（raw.with_retry(...)）
    _breaker: CircuitBreaker = PrivateAttr()
    _timeout_s: float = PrivateAttr(default=10.0)
    # 熔断打开 / 重试耗尽后的降级结果；None 则抛 ToolExecutionError
    _fallback: dict[str, Any] | None = PrivateAttr(default=None)
    # 是否参与结果缓存白名单（实际白名单以 cached_tools() 名单为准）
    _cache_enabled: bool = PrivateAttr(default=True)

    def __init__(
        self,
        *,
        inner: Any,
        breaker: CircuitBreaker,
        timeout_s: float = 10.0,
        fallback: dict[str, Any] | None = None,
        cache_enabled: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._inner = inner
        self._breaker = breaker
        self._timeout_s = timeout_s
        self._fallback = fallback
        self._cache_enabled = cache_enabled

    @property
    def breaker_state(self) -> _BreakerState:
        """查询熔断状态（可观测性 / 测试用）。"""
        return self._breaker.state

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """同步壳：守卫工具仅支持异步调用（langchain 异步工具标准做法）。"""
        raise NotImplementedError("GuardedTool 仅支持 ainvoke（异步路径）")

    async def _arun(self, **kwargs: Any) -> dict[str, Any]:
        from services.cache import cached_tools, get_tool_cache

        # 1. 幂等工具结果缓存（T028）：入参一致直接返回，不计入熔断/耗时
        if self._cache_enabled and self.name in cached_tools():
            cache_input = dict(kwargs)
            cache = await get_tool_cache()
            cached = await cache.get(self.name, cache_input)
            if cached is not None:
                metrics.record_tool_cache(self.name, "hit")
                return cached
            metrics.record_tool_cache(self.name, "miss")

        # 2. 熔断检查
        if not self._breaker.allow_call():
            log.warning("tool_call_rejected_by_breaker", tool=self.name, state=self._breaker.state)
            metrics.record_breaker_rejected(self.name)
            if self._fallback is not None:
                metrics.record_tool_call(self.name, "fallback", 0.0)
                return dict(self._fallback)
            raise ToolExecutionError(
                self.name, f"熔断中（连续失败 {self._breaker.consecutive_failures} 次）"
            )

        # 3. 超时（总预算）+ 内层执行（内层已由 factory 套 with_retry）
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._timeout_s):
                result = await self._inner.ainvoke(kwargs)
        except Exception as exc:  # noqa: BLE001 守卫层统一收口
            self._breaker.record_failure()
            # 配置了 fallback 则降级返回（计 fallback 态），否则计 error 并抛错
            if self._fallback is not None:
                metrics.record_tool_call(self.name, "fallback", time.perf_counter() - started)
                return dict(self._fallback)
            metrics.record_tool_call(self.name, "error", time.perf_counter() - started)
            if isinstance(exc, TimeoutError):
                log.warning("tool_timeout", tool=self.name, timeout_s=self._timeout_s)
                raise ToolExecutionError(
                    self.name, f"{self.name} 超时（>{self._timeout_s}s）"
                ) from exc
            raise ToolExecutionError(self.name, str(exc)[:300]) from exc

        self._breaker.record_success()
        metrics.record_tool_call(self.name, "success", time.perf_counter() - started)

        # 4. 成功结果回写缓存（仅白名单工具）
        if self._cache_enabled and self.name in cached_tools() and result.get("success", True):
            await (await get_tool_cache()).set(self.name, dict(kwargs), result)
        return result
