"""ToolExecutor 工具执行器：超时 / 重试 / 熔断统一保障（architecture.md 4.3）。

- 超时控制：每个工具调用独立超时（默认 10s，可按工具覆盖）
- 重试策略：瞬时故障（超时、可重试异常）指数退避重试，最多 2 次
- 熔断保护：单工具连续失败 5 次 → 熔断 30s（期间直接拒绝并返回 Fallback），
  冷却后半开放行探测：成功关闭熔断器，失败继续熔断
"""

from __future__ import annotations

import asyncio
import time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from app.core.exceptions import ToolExecutionError
from app.core.logging import get_logger
from schemas.tools import ToolOutput
from tools.registry import ToolRegistry

log = get_logger(__name__)


class _BreakerState(StrEnum):
    """熔断器状态（architecture.md 7.2）。"""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _CircuitBreaker:
    """单工具熔断器：5 次连续失败 → open 30s → half-open 探测。"""

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
        if self.state == _BreakerState.HALF_OPEN or self.consecutive_failures >= self.failure_threshold:
            self.state = _BreakerState.OPEN
            self.opened_at = time.monotonic()
            log.warning(
                "circuit_breaker_opened",
                consecutive_failures=self.consecutive_failures,
                cooldown_s=self.cooldown_seconds,
            )


class ToolExecutor:
    """统一工具执行入口：Agent / 节点不直接调工具，一律经此执行。"""

    def __init__(
        self,
        registry: ToolRegistry,
        default_timeout: float = 10.0,
        max_retries: int = 2,
        retry_backoff_base: float = 0.5,
        failure_threshold: int = 5,
        breaker_cooldown: float = 30.0,
    ) -> None:
        self.registry = registry
        self.default_timeout = default_timeout
        self.max_retries = max_retries
        self.retry_backoff_base = retry_backoff_base
        self.failure_threshold = failure_threshold
        self.breaker_cooldown = breaker_cooldown
        self._breakers: dict[str, _CircuitBreaker] = {}

    def _breaker(self, tool_name: str) -> _CircuitBreaker:
        if tool_name not in self._breakers:
            self._breakers[tool_name] = _CircuitBreaker(
                failure_threshold=self.failure_threshold,
                cooldown_seconds=self.breaker_cooldown,
            )
        return self._breakers[tool_name]

    async def execute(
        self,
        tool_name: str,
        input_data: dict[str, Any] | BaseModel,
        *,
        timeout: float | None = None,
        fallback: ToolOutput | None = None,
    ) -> ToolOutput:
        """执行工具：熔断检查 → 重试循环（超时 + 指数退避）→ 熔断计数。

        Args:
            tool_name: 注册中心中的工具名
            input_data: LLM 给出的工具入参（dict 或已校验模型）
            timeout: 本次调用超时秒数，缺省用 default_timeout
            fallback: 熔断打开 / 重试耗尽后的降级结果；None 则抛 ToolExecutionError
        """
        tool = self.registry.get(tool_name)
        breaker = self._breaker(tool_name)

        if not breaker.allow_call():
            log.warning("tool_call_rejected_by_breaker", tool=tool_name, state=breaker.state)
            if fallback is not None:
                return fallback
            raise ToolExecutionError(tool_name, f"熔断中（连续失败 {breaker.consecutive_failures} 次）")

        effective_timeout = timeout if timeout is not None else self.default_timeout
        last_error: Exception | None = None

        # 总尝试次数 = 1 次初始 + max_retries 次重试
        for attempt in range(self.max_retries + 1):
            try:
                async with asyncio.timeout(effective_timeout):
                    result = await tool.execute(input_data)
                breaker.record_success()
                return result
            except TimeoutError:
                last_error = TimeoutError(f"{tool_name} 超时（>{effective_timeout}s）")
                log.warning("tool_timeout", tool=tool_name, attempt=attempt, timeout_s=effective_timeout)
            except ToolExecutionError as exc:
                # 业务工具主动抛出的执行错误：视为不可重试，直接终止
                last_error = exc
                break
            except Exception as exc:
                # 其他异常（网络、依赖故障）视为瞬时故障，可重试
                last_error = exc
                log.warning("tool_error_retryable", tool=tool_name, attempt=attempt, error=str(exc)[:200])

            if attempt < self.max_retries:
                backoff = self.retry_backoff_base * (2**attempt)
                await asyncio.sleep(backoff)

        breaker.record_failure()
        if fallback is not None:
            return fallback
        raise ToolExecutionError(tool_name, str(last_error))

    def breaker_state(self, tool_name: str) -> _BreakerState:
        """查询工具熔断状态（可观测性 / 测试用）。"""
        return self._breaker(tool_name).state
