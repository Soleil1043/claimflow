"""工具层基础设施测试（T044 重写；T094 删 registry/executor 段）：ClaimflowTool / 工厂装配守卫。

用 EchoTool / 可控故障工具验证：
- ainvoke 入参校验（ValidationError）与业务 dict 返回
- to_openai_tool 生成 function calling 定义
- 默认工具图工厂装配
- 守卫层（tools/guards.py，经 factory 装配）：
  超时（总预算）、官方 .with_retry 重试（≤2 次）、
  熔断（5 失败 → open → half-open 探测 → 恢复/复开）、计数清零
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import PrivateAttr, ValidationError

from app.core.exceptions import ToolExecutionError
from schemas.tools import ToolInput
from tools.base import ClaimflowTool
from tools.factory import assemble_tool, get_default_tool_map
from tools.guards import GuardedTool, _BreakerState

# ---------- 测试用工具（新基类） ----------


class EchoInput(ToolInput):
    text: str


class EchoTool(ClaimflowTool):
    name: str = "echo"
    description: str = "原样返回输入文本（测试用）"
    args_schema: type[EchoInput] = EchoInput

    def _run(self, *args: object, **kwargs: object) -> dict:
        raise NotImplementedError("仅支持异步调用")

    async def _arun(self, *, text: str) -> dict:
        return {"success": True, "text": text}


class FlakyInput(ToolInput):
    fail_times: int = 0
    delay: float = 0.0


class FlakyTool(ClaimflowTool):
    """可控故障工具：前 fail_times 次抛异常，之后成功；delay 模拟慢调用。"""

    name: str = "flaky"
    description: str = "可控故障工具（测试用）"
    args_schema: type[FlakyInput] = FlakyInput

    _calls: int = PrivateAttr(default=0)

    @property
    def calls(self) -> int:
        return self._calls

    def reset(self) -> None:
        self._calls = 0

    def _run(self, *args: object, **kwargs: object) -> dict:
        raise NotImplementedError("仅支持异步调用")

    async def _arun(self, *, fail_times: int = 0, delay: float = 0.0) -> dict:
        self._calls += 1
        if delay:
            await asyncio.sleep(delay)
        if self._calls <= fail_times:
            msg = f"模拟故障 第{self._calls}次"
            raise RuntimeError(msg)
        return {"success": True, "call": self._calls}


def _guarded_flaky(**guard_kwargs: object) -> tuple[GuardedTool, FlakyTool]:
    """装配守卫版 flaky（测试用极小退避/冷却），返回（守卫工具, 原工具）。"""
    raw = FlakyTool()
    defaults: dict = {
        "timeout_s": 10.0,
        "max_retries": 2,
        "backoff_initial": 0.001,
        "failure_threshold": 5,
        "breaker_cooldown": 0.05,
        "enable_cache": False,
    }
    defaults.update(guard_kwargs)
    return assemble_tool(raw, **defaults), raw


# ---------- ClaimflowTool ----------


async def test_tool_ainvoke_returns_dict() -> None:
    """ainvoke：dict 入参经 args_schema 校验后执行，返回业务 dict。"""
    result = await EchoTool().ainvoke({"text": "你好"})
    assert result == {"success": True, "text": "你好"}


async def test_tool_invalid_input_raises_validation_error() -> None:
    """raw 工具：非法入参抛 ValidationError（守卫/兼容壳负责降级）。"""
    with pytest.raises(ValidationError):
        await EchoTool().ainvoke({"wrong_field": 1})


def test_to_openai_tool_schema() -> None:
    """to_openai_tool：生成 OpenAI function calling 定义。"""
    definition = EchoTool().to_openai_tool()
    assert definition["type"] == "function"
    fn = definition["function"]
    assert fn["name"] == "echo"
    assert "返回输入" in fn["description"]
    assert fn["parameters"]["properties"]["text"]["type"] == "string"
    assert fn["parameters"]["required"] == ["text"]


# ---------- 默认工具图 ----------


def test_default_tool_map_factory_assembly() -> None:
    """默认工具图：工厂装配 12 个守卫工具（官方重试 + 熔断/缓存/超时）。"""
    tool_map = get_default_tool_map()
    assert len(tool_map) == 12
    assert "policy_query" in tool_map and "risk_scoring" in tool_map
    guarded = tool_map["policy_query"]
    assert isinstance(guarded, GuardedTool)
    spec = guarded.to_openai_tool()
    assert spec["function"]["name"] == "policy_query"


# ---------- 守卫：超时 ----------


async def test_guard_timeout_then_error() -> None:
    """超时（总预算）：慢调用直接失败，抛 ToolExecutionError 含超时说明。"""
    guarded, _ = _guarded_flaky(timeout_s=0.05)
    with pytest.raises(ToolExecutionError, match="超时"):
        await guarded.ainvoke({"delay": 1.0})


async def test_guard_timeout_with_fallback() -> None:
    """守卫级 fallback：超时/熔断时返回降级 dict 而非抛错。"""
    guarded, _ = _guarded_flaky(
        timeout_s=0.05, fallback={"success": False, "error_message": "服务暂不可用"}
    )
    result = await guarded.ainvoke({"delay": 1.0})
    assert result["success"] is False
    assert result["error_message"] == "服务暂不可用"


# ---------- 守卫：重试（官方 .with_retry，装配于 factory） ----------


async def test_guard_retries_then_succeeds() -> None:
    """瞬时故障：前 2 次失败、第 3 次成功 → 重试后成功（总尝试 3 次）。"""
    guarded, raw = _guarded_flaky()
    result = await guarded.ainvoke({"fail_times": 2})
    assert result["success"] is True
    assert raw.calls == 3


async def test_guard_retry_budget_exhausted() -> None:
    """重试上限：初始 1 次 + 重试 2 次 = 3 次尝试，仍失败则抛错。"""
    guarded, raw = _guarded_flaky()
    with pytest.raises(ToolExecutionError, match="模拟故障"):
        await guarded.ainvoke({"fail_times": 99})
    assert raw.calls == 3


async def test_guard_success_no_retry() -> None:
    """成功调用零重试。"""
    guarded, raw = _guarded_flaky()
    result = await guarded.ainvoke({})
    assert result["success"] is True
    assert raw.calls == 1


# ---------- 守卫：熔断 ----------


async def test_circuit_breaker_opens_after_5_failures() -> None:
    """连续 5 轮调用失败（每轮含重试）→ 熔断打开，后续直接拒绝。"""
    guarded, raw = _guarded_flaky(breaker_cooldown=999)

    # 5 轮全失败（每轮 3 次尝试，均抛错）
    for _ in range(5):
        with pytest.raises(ToolExecutionError):
            await guarded.ainvoke({"fail_times": 99})

    assert guarded.breaker_state == _BreakerState.OPEN
    calls_before = raw.calls

    # 熔断打开：直接拒绝，工具零调用
    with pytest.raises(ToolExecutionError, match="熔断中"):
        await guarded.ainvoke({"fail_times": 0})
    assert raw.calls == calls_before


async def test_circuit_breaker_fallback_when_open() -> None:
    """熔断打开时守卫级 fallback：全程降级返回而非抛错（失败路径同样计熔断数）。"""
    guarded, _ = _guarded_flaky(
        breaker_cooldown=999,
        fallback={"success": False, "error_message": "RAG 暂不可用，返回兜底模板"},
    )
    # 5 轮失败：配置 fallback 后降级返回（不再抛错），但每轮仍累计熔断计数
    for _ in range(5):
        result = await guarded.ainvoke({"fail_times": 99})
        assert result["success"] is False
    assert guarded.breaker_state == _BreakerState.OPEN

    # 熔断打开：直接拒绝，返回降级结果
    result = await guarded.ainvoke({"fail_times": 0})
    assert result["success"] is False
    assert "兜底" in (result["error_message"] or "")


async def test_circuit_breaker_half_open_recovery() -> None:
    """冷却期过后 half-open 放行探测：成功 → 熔断器关闭恢复。"""
    guarded, _ = _guarded_flaky(breaker_cooldown=0.05)
    for _ in range(5):
        with pytest.raises(ToolExecutionError):
            await guarded.ainvoke({"fail_times": 99})
    assert guarded.breaker_state == _BreakerState.OPEN

    await asyncio.sleep(0.5)  # 越过冷却期（cooldown 的 10 倍余量，抗 CI 慢调度）
    # 探测成功（fail_times=0）：熔断器关闭
    result = await guarded.ainvoke({"fail_times": 0})
    assert result["success"] is True
    assert guarded.breaker_state == _BreakerState.CLOSED


async def test_circuit_breaker_half_open_failure_reopens() -> None:
    """half-open 探测失败 → 立即回到 open。"""
    guarded, _ = _guarded_flaky(breaker_cooldown=0.05)
    for _ in range(5):
        with pytest.raises(ToolExecutionError):
            await guarded.ainvoke({"fail_times": 99})
    await asyncio.sleep(0.5)  # cooldown 的 10 倍余量

    # 探测仍失败
    with pytest.raises(ToolExecutionError):
        await guarded.ainvoke({"fail_times": 99})
    assert guarded.breaker_state == _BreakerState.OPEN


async def test_breaker_failure_counter_resets_on_success() -> None:
    """成功会清零失败计数：4 次失败 + 1 次成功 + 4 次失败 → 仍未熔断。"""
    guarded, raw = _guarded_flaky(breaker_cooldown=999)

    for _ in range(4):
        with pytest.raises(ToolExecutionError):
            await guarded.ainvoke({"fail_times": 99})
    raw.reset()

    assert (await guarded.ainvoke({"fail_times": 0}))["success"] is True
    raw.reset()
    for _ in range(4):
        with pytest.raises(ToolExecutionError):
            await guarded.ainvoke({"fail_times": 99})
    assert guarded.breaker_state == _BreakerState.CLOSED
