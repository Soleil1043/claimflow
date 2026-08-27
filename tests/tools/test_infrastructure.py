"""工具层基础设施测试：BaseTool / ToolRegistry / ToolExecutor。

用 EchoTool / 可控故障工具验证：
- execute 入参校验与日志路径
- to_openai_tool 生成 function calling 定义
- 注册 / 发现 / 重名拒绝
- 超时控制、指数退避重试（≤2 次）、熔断（5 失败 → open 30s → half-open 探测）
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.exceptions import ToolExecutionError
from schemas.tools import ToolInput, ToolOutput
from tools.base import BaseTool
from tools.executor import ToolExecutor, _BreakerState
from tools.registry import ToolNotFoundError, ToolRegistry

# ---------- 测试用工具 ----------


class EchoInput(ToolInput):
    text: str


class EchoOutput(ToolOutput):
    pass


class EchoTool(BaseTool[EchoInput, EchoOutput]):
    name = "echo"
    description = "原样返回输入文本（测试用）"
    input_schema = EchoInput
    output_schema = EchoOutput

    async def _run(self, input_data: EchoInput) -> EchoOutput:
        return EchoOutput(success=True, data={"text": input_data.text})


class FlakyInput(ToolInput):
    fail_times: int = 0
    delay: float = 0.0


class FlakyOutput(ToolOutput):
    pass


class FlakyTool(BaseTool[FlakyInput, FlakyOutput]):
    """可控故障工具：前 fail_times 次抛异常，之后成功；delay 模拟慢调用。"""

    name = "flaky"
    description = "可控故障工具（测试用）"
    input_schema = FlakyInput
    output_schema = FlakyOutput

    def __init__(self) -> None:
        self.calls = 0

    async def _run(self, input_data: FlakyInput) -> FlakyOutput:
        self.calls += 1
        if input_data.delay:
            await asyncio.sleep(input_data.delay)
        if self.calls <= input_data.fail_times:
            msg = f"模拟故障 第{self.calls}次"
            raise RuntimeError(msg)
        return FlakyOutput(success=True, data={"call": self.calls})


@pytest.fixture()
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(EchoTool())
    reg.register(FlakyTool())
    return reg


@pytest.fixture()
def executor(registry: ToolRegistry) -> ToolExecutor:
    # 测试用：退避基数压到极小，不真实等待
    return ToolExecutor(registry, retry_backoff_base=0.001, breaker_cooldown=0.05)


# ---------- BaseTool ----------


async def test_base_tool_execute_validates_and_runs() -> None:
    """execute：dict 入参经 schema 校验后执行，输出 success。"""
    tool = EchoTool()
    result = await tool.execute({"text": "你好"})
    assert result.success is True
    assert result.data == {"text": "你好"}


async def test_base_tool_execute_invalid_input() -> None:
    """execute：非法入参（缺字段）返回 success=False，不抛异常。"""
    tool = EchoTool()
    result = await tool.execute({"wrong_field": 1})
    assert result.success is False
    assert "入参校验失败" in (result.error_message or "")


def test_to_openai_tool_schema() -> None:
    """to_openai_tool：生成 OpenAI function calling 定义。"""
    definition = EchoTool().to_openai_tool()
    assert definition["type"] == "function"
    fn = definition["function"]
    assert fn["name"] == "echo"
    assert "返回输入" in fn["description"]
    assert fn["parameters"]["properties"]["text"]["type"] == "string"
    assert fn["parameters"]["required"] == ["text"]


# ---------- ToolRegistry ----------


def test_registry_register_and_get(registry: ToolRegistry) -> None:
    """注册后可按名获取，list_names 排序输出。"""
    assert registry.get("echo").name == "echo"
    assert registry.list_names() == ["echo", "flaky"]


def test_registry_rejects_duplicate(registry: ToolRegistry) -> None:
    """重复注册同名工具直接抛错。"""
    with pytest.raises(ValueError, match="重复注册"):
        registry.register(EchoTool())


def test_registry_unknown_tool(registry: ToolRegistry) -> None:
    """未注册工具抛 ToolNotFoundError。"""
    with pytest.raises(ToolNotFoundError):
        registry.get("no_such_tool")


def test_registry_to_openai_tools(registry: ToolRegistry) -> None:
    """批量导出 / 按名过滤导出 OpenAI 工具定义。"""
    all_tools = registry.to_openai_tools()
    assert {t["function"]["name"] for t in all_tools} == {"echo", "flaky"}

    only_echo = registry.to_openai_tools(names=["echo"])
    assert len(only_echo) == 1
    assert only_echo[0]["function"]["name"] == "echo"


# ---------- ToolExecutor：超时 ----------


async def test_executor_timeout_then_error(registry: ToolRegistry) -> None:
    """超时不重试成功路径：每次都慢 → 重试耗尽后抛 ToolExecutionError。"""
    ex = ToolExecutor(registry, default_timeout=0.05, retry_backoff_base=0.001)
    with pytest.raises(ToolExecutionError, match="超时"):
        await ex.execute("flaky", {"delay": 1.0})


async def test_executor_timeout_falls_back(registry: ToolRegistry) -> None:
    """给定 fallback 时超时不抛错，返回降级结果。"""
    ex = ToolExecutor(registry, default_timeout=0.05, retry_backoff_base=0.001)
    fallback = ToolOutput(success=False, error_message="服务暂不可用")
    result = await ex.execute("flaky", {"delay": 1.0}, fallback=fallback)
    assert result.success is False
    assert result.error_message == "服务暂不可用"


# ---------- ToolExecutor：重试 ----------


async def test_executor_retries_then_succeeds(
    registry: ToolRegistry, executor: ToolExecutor
) -> None:
    """瞬时故障：前 2 次失败、第 3 次成功 → 重试后成功（总尝试 3 次）。"""
    tool = registry.get("flaky")
    assert isinstance(tool, FlakyTool)
    result = await executor.execute("flaky", {"fail_times": 2})
    assert result.success is True
    assert tool.calls == 3


async def test_executor_retry_budget_exhausted(registry: ToolRegistry) -> None:
    """重试上限：初始 1 次 + 重试 2 次 = 3 次尝试，仍失败则抛错。"""
    ex = ToolExecutor(registry, retry_backoff_base=0.001)
    tool = registry.get("flaky")
    assert isinstance(tool, FlakyTool)
    with pytest.raises(ToolExecutionError, match="模拟故障"):
        await ex.execute("flaky", {"fail_times": 99})
    assert tool.calls == 3


async def test_executor_success_no_retry(executor: ToolExecutor, registry: ToolRegistry) -> None:
    """成功调用零重试。"""
    tool = registry.get("flaky")
    assert isinstance(tool, FlakyTool)
    result = await executor.execute("flaky", {})
    assert result.success is True
    assert tool.calls == 1


# ---------- ToolExecutor：熔断 ----------


async def test_circuit_breaker_opens_after_5_failures(registry: ToolRegistry) -> None:
    """连续 5 轮调用失败（每轮含重试）→ 熔断打开，后续直接拒绝。"""
    ex = ToolExecutor(registry, retry_backoff_base=0.001, breaker_cooldown=999)
    tool = registry.get("flaky")
    assert isinstance(tool, FlakyTool)

    # 5 轮全失败（每轮 3 次尝试，均抛错）
    for _ in range(5):
        with pytest.raises(ToolExecutionError):
            await ex.execute("flaky", {"fail_times": 99})

    assert ex.breaker_state("flaky") == _BreakerState.OPEN
    calls_before = tool.calls

    # 熔断打开：直接拒绝，工具零调用
    with pytest.raises(ToolExecutionError, match="熔断中"):
        await ex.execute("flaky", {"fail_times": 0})
    assert tool.calls == calls_before


async def test_circuit_breaker_fallback_when_open(registry: ToolRegistry) -> None:
    """熔断打开时提供 fallback：返回降级结果而非抛错。"""
    ex = ToolExecutor(registry, retry_backoff_base=0.001, breaker_cooldown=999)
    for _ in range(5):
        with pytest.raises(ToolExecutionError):
            await ex.execute("flaky", {"fail_times": 99})

    fallback = ToolOutput(success=False, error_message="RAG 暂不可用，返回兜底模板")
    result = await ex.execute("flaky", {"fail_times": 0}, fallback=fallback)
    assert result.success is False
    assert "兜底" in (result.error_message or "")


async def test_circuit_breaker_half_open_recovery(registry: ToolRegistry) -> None:
    """冷却期过后 half-open 放行探测：成功 → 熔断器关闭恢复。"""
    ex = ToolExecutor(registry, retry_backoff_base=0.001, breaker_cooldown=0.05)
    for _ in range(5):
        with pytest.raises(ToolExecutionError):
            await ex.execute("flaky", {"fail_times": 99})
    assert ex.breaker_state("flaky") == _BreakerState.OPEN

    await asyncio.sleep(0.5)  # 越过冷却期（cooldown 的 10 倍余量，抗 CI 慢调度）
    # 探测成功（fail_times=0）：熔断器关闭
    result = await ex.execute("flaky", {"fail_times": 0})
    assert result.success is True
    assert ex.breaker_state("flaky") == _BreakerState.CLOSED


async def test_circuit_breaker_half_open_failure_reopens(registry: ToolRegistry) -> None:
    """half-open 探测失败 → 立即回到 open。"""
    ex = ToolExecutor(registry, retry_backoff_base=0.001, breaker_cooldown=0.05)
    for _ in range(5):
        with pytest.raises(ToolExecutionError):
            await ex.execute("flaky", {"fail_times": 99})
    await asyncio.sleep(0.5)  # cooldown 的 10 倍余量

    # 探测仍失败
    with pytest.raises(ToolExecutionError):
        await ex.execute("flaky", {"fail_times": 99})
    assert ex.breaker_state("flaky") == _BreakerState.OPEN


async def test_breaker_failure_counter_resets_on_success(registry: ToolRegistry) -> None:
    """成功会清零失败计数：4 次失败 + 1 次成功 + 4 次失败 → 仍未熔断。"""
    ex = ToolExecutor(registry, retry_backoff_base=0.001, breaker_cooldown=999)

    for _ in range(4):
        with pytest.raises(ToolExecutionError):
            await ex.execute("flaky", {"fail_times": 99})
    tool = registry.get("flaky")
    assert isinstance(tool, FlakyTool)
    tool.calls = 0  # 重置计数以便 fail_times 生效

    assert (await ex.execute("flaky", {"fail_times": 0})).success is True
    tool.calls = 0
    for _ in range(4):
        with pytest.raises(ToolExecutionError):
            await ex.execute("flaky", {"fail_times": 99})
    assert ex.breaker_state("flaky") == _BreakerState.CLOSED


async def test_executor_unknown_tool_raises(executor: ToolExecutor) -> None:
    """未注册工具：执行器直接抛 ToolNotFoundError。"""
    with pytest.raises(ToolNotFoundError):
        await executor.execute("no_such_tool", {})
