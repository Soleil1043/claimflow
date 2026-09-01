"""T028 工具结果缓存测试：命中 / 过期 / 禁用三态 + key 规范 + 守卫层集成（T044）。"""

from __future__ import annotations

from prometheus_client import REGISTRY
from pydantic import BaseModel, PrivateAttr

from services.cache import (
    MemoryToolCache,
    ToolResultCache,
    cached_tools,
    get_tool_cache,
    reset_tool_cache,
)
from tools.base import ClaimflowTool
from tools.executor import ToolExecutor
from tools.factory import assemble_tool
from tools.registry import ToolRegistry


def _counter(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


# ===== 缓存服务本体 =====


async def test_cache_hit() -> None:
    """相同入参二次 get 命中。"""
    cache = ToolResultCache(MemoryToolCache())
    await cache.set("policy_query", {"policy_no": "POL-2025-0001"}, {"success": True, "data": {"x": 1}})
    hit = await cache.get("policy_query", {"policy_no": "POL-2025-0001"})
    assert hit is not None and hit["success"] is True


async def test_cache_miss_on_different_input() -> None:
    """入参不同不命中（key 含入参指纹）。"""
    cache = ToolResultCache(MemoryToolCache())
    await cache.set("policy_query", {"policy_no": "A"}, {"success": True})
    assert await cache.get("policy_query", {"policy_no": "B"}) is None


async def test_cache_expiry() -> None:
    """TTL 过期后不命中（内存后端直接操纵过期时间模拟）。"""
    backend = MemoryToolCache()
    cache = ToolResultCache(backend)
    await cache.set("policy_query", {"a": 1}, {"success": True})
    # 篡改过期时间到过去
    key = cache.make_key("policy_query", {"a": 1})
    backend._store[key] = (backend._store[key][0], backend._store[key][1] - 9999)
    assert await cache.get("policy_query", {"a": 1}) is None


async def test_cache_key_canonical() -> None:
    """key 与键序无关：同字典不同插入顺序 → 同 key。"""
    k1 = ToolResultCache.make_key("t", {"a": 1, "b": 2})
    k2 = ToolResultCache.make_key("t", {"b": 2, "a": 1})
    assert k1 == k2


async def test_cache_disabled_backend(monkeypatch) -> None:
    """enabled=False 时为无操作后端：永不命中。

    注意 monkeypatch 到 services.cache 模块引用的 settings：
    test_logging.py 会 reload app.core.config 产生新单例，
    直接改 app.core.config.settings 对本模块不生效（跨实例陷阱）。
    """
    import services.cache as cache_module

    monkeypatch.setattr(cache_module.settings, "tool_cache_enabled", False)
    reset_tool_cache()
    try:
        cache = await get_tool_cache()
        await cache.set("t", {}, {"success": True})
        assert await cache.get("t", {}) is None
    finally:
        reset_tool_cache()


def test_cached_tools_whitelist() -> None:
    """白名单解析：policy_query 在，claim_calculator（有副作用语义）不在。"""
    wl = cached_tools()
    assert "policy_query" in wl
    assert "claim_calculator" not in wl


# ===== 守卫层集成（缓存经 GuardedTool 生效，T044 下沉工具层） =====


class _SlowQueryInput(BaseModel):
    policy_no: str = "POL-2025-0001"


class _SlowQueryTool(ClaimflowTool):
    """计数执行的幂等查询工具。"""

    name: str = "policy_query"
    description: str = "测试用查询工具"
    args_schema: type[_SlowQueryInput] = _SlowQueryInput
    _calls: int = PrivateAttr(default=0)

    @property
    def calls(self) -> int:
        return self._calls

    def _run(self, *args: object, **kwargs: object) -> dict:
        raise NotImplementedError("仅支持异步调用")

    async def _arun(self, *, policy_no: str = "POL-2025-0001") -> dict:
        self._calls += 1
        return {"success": True, "policy_no": policy_no, "n": self._calls}


class _CalcTool(ClaimflowTool):
    """非白名单工具（不缓存）。"""

    name: str = "claim_calculator"
    description: str = "测试用计算工具"
    args_schema: type[_SlowQueryInput] = _SlowQueryInput
    _calls: int = PrivateAttr(default=0)

    @property
    def calls(self) -> int:
        return self._calls

    def _run(self, *args: object, **kwargs: object) -> dict:
        raise NotImplementedError("仅支持异步调用")

    async def _arun(self, *, policy_no: str = "POL-2025-0001") -> dict:
        self._calls += 1
        return {"success": True, "n": self._calls}


def _executor_with(raw: ClaimflowTool) -> tuple[ToolExecutor, ClaimflowTool]:
    """守卫装配（含缓存白名单逻辑）+ 兼容壳，返回（executor, 原工具）。"""
    guarded = assemble_tool(raw, backoff_initial=0.001)
    registry = ToolRegistry()
    registry.register(guarded)
    return ToolExecutor(registry), raw


async def test_guard_cache_hit_second_call() -> None:
    """白名单工具相同入参：第二次走缓存（真实执行只发生一次）。"""
    reset_tool_cache()
    executor, slow = _executor_with(_SlowQueryTool())

    r1 = await executor.execute("policy_query", {"policy_no": "POL-2025-0001"})
    r2 = await executor.execute("policy_query", {"policy_no": "POL-2025-0001"})

    assert slow.calls == 1, "第二次应命中缓存，不再真实执行"
    assert r1.success and r2.success
    assert r2.data == r1.data  # 返回的是缓存的首个结果
    hit = _counter("claimflow_tool_cache_hits_total", tool="policy_query", result="hit")
    assert hit >= 1.0


async def test_guard_cache_not_for_uncached_tools() -> None:
    """非白名单工具：每次真实执行，无缓存指标。"""
    reset_tool_cache()
    executor, calc = _executor_with(_CalcTool())

    await executor.execute("claim_calculator", {"policy_no": "A"})
    await executor.execute("claim_calculator", {"policy_no": "A"})

    assert calc.calls == 2, "非白名单工具不缓存"
    assert _counter("claimflow_tool_cache_hits_total", tool="claim_calculator", result="hit") == 0.0


async def test_guard_cache_metrics_miss_then_hit() -> None:
    """指标三态：miss（首次）→ hit（二次）。"""
    reset_tool_cache()
    executor, _slow = _executor_with(_SlowQueryTool())

    await executor.execute("policy_query", {"policy_no": "X-1"})
    miss = _counter("claimflow_tool_cache_hits_total", tool="policy_query", result="miss")
    await executor.execute("policy_query", {"policy_no": "X-1"})
    hit = _counter("claimflow_tool_cache_hits_total", tool="policy_query", result="hit")
    assert miss >= 1.0 and hit >= 1.0


async def test_guard_failed_result_not_cached() -> None:
    """业务失败（success=False）不回写缓存：下次仍真实执行。"""

    class _FlakyInput(BaseModel):
        q: str = "x"

    class _FlakyTool(ClaimflowTool):
        name: str = "claim_rule_rag"
        description: str = "先失败后成功的工具"
        args_schema: type[_FlakyInput] = _FlakyInput
        _calls: int = PrivateAttr(default=0)

        @property
        def calls(self) -> int:
            return self._calls

        def _run(self, *args: object, **kwargs: object) -> dict:
            raise NotImplementedError("仅支持异步调用")

        async def _arun(self, *, q: str = "x") -> dict:
            self._calls += 1
            ok = self._calls >= 2
            return {"success": ok} if ok else {"success": False, "error_message": "检索无结果"}

    reset_tool_cache()
    executor, flaky = _executor_with(_FlakyTool())
    r1 = await executor.execute("claim_rule_rag", {"q": "x"})
    r2 = await executor.execute("claim_rule_rag", {"q": "x"})
    assert not r1.success and r2.success
    assert flaky.calls == 2, "失败结果不应被缓存"
