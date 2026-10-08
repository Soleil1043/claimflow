"""T024 Prometheus 指标埋点测试：注册、打点维度、容错、/metrics 端点。"""

from __future__ import annotations

import asyncio
from typing import Any

from httpx import ASGITransport, AsyncClient
from prometheus_client import REGISTRY
from pydantic import BaseModel

from services.observability import metrics
from services.observability.llm_metrics import observed_ainvoke
from tools.base import ClaimflowTool
from tools.factory import assemble_tool


def _counter_value(name: str, **labels: str) -> float:
    """读全局 REGISTRY 中某 Counter 的当前值。"""
    return REGISTRY.get_sample_value(name, labels) or 0.0


# ===== metrics 模块：指标注册与打点函数 =====


def test_metrics_registered() -> None:
    """所有核心指标已在全局 REGISTRY 注册（/metrics 可暴露）。"""
    # 至少打一次点让带标签的样本出现
    metrics.record_tool_call("policy_query", "success", 0.1)
    metrics.record_breaker_rejected("ocr_extract")
    metrics.record_llm_call("deepseek-flash", "success", 0.5, prompt_tokens=10, completion_tokens=5)

    assert _counter_value(
        "claimflow_tool_calls_total", tool="policy_query", status="success"
    ) >= 1.0
    assert _counter_value("claimflow_tool_breaker_rejected_total", tool="ocr_extract") >= 1.0
    assert _counter_value(
        "claimflow_llm_calls_total", model="deepseek-flash", status="success"
    ) >= 1.0
    assert _counter_value(
        "claimflow_llm_tokens_total", model="deepseek-flash", kind="prompt"
    ) >= 10.0
    assert _counter_value(
        "claimflow_llm_tokens_total", model="deepseek-flash", kind="completion"
    ) >= 5.0


def test_record_llm_call_without_tokens() -> None:
    """usage 缺失（token 参数为 None）时不记 token 维度，也不报错。"""
    before = _counter_value(
        "claimflow_llm_tokens_total", model="no-usage-model", kind="prompt"
    )
    metrics.record_llm_call("no-usage-model", "success", 0.1)
    assert (
        _counter_value("claimflow_llm_tokens_total", model="no-usage-model", kind="prompt")
        == before
    )


# ===== Prompt Caching 命中指标（T162）=====


def test_record_llm_call_with_cache_tokens() -> None:
    """cache_hit/miss 非 None 时按 hit/miss 分维记数（DeepSeek 非标字段透传）。"""
    metrics.record_llm_call(
        "deepseek-flash",
        "success",
        0.5,
        prompt_tokens=100,
        completion_tokens=50,
        cache_hit_tokens=80,
        cache_miss_tokens=20,
    )
    assert _counter_value(
        "claimflow_llm_cache_tokens_total", model="deepseek-flash", stage="other", result="hit"
    ) >= 80.0
    assert _counter_value(
        "claimflow_llm_cache_tokens_total", model="deepseek-flash", stage="other", result="miss"
    ) >= 20.0


def test_record_llm_call_cache_tokens_optional() -> None:
    """cache 参数缺省（None）时不记缓存维度，也不报错——不臆造 0。"""
    before_hit = _counter_value(
        "claimflow_llm_cache_tokens_total", model="no-cache-model", stage="other", result="hit"
    )
    metrics.record_llm_call("no-cache-model", "success", 0.1, prompt_tokens=10)
    assert (
        _counter_value(
            "claimflow_llm_cache_tokens_total", model="no-cache-model", stage="other", result="hit"
        )
        == before_hit
    )


# ===== 守卫层集成（T044）：工具三态 + 熔断埋点（metrics 经 GuardedTool 打点） =====


class _OkInput(BaseModel):
    x: int = 1


class _OkTool(ClaimflowTool):
    name: str = "metrics_ok_tool"
    description: str = "总是成功的测试工具"
    args_schema: type[_OkInput] = _OkInput

    def _run(self, *args: object, **kwargs: object) -> dict:
        raise NotImplementedError("仅支持异步调用")

    async def _arun(self, *, x: int = 1) -> dict:
        return {"success": True, "value": x}


class _FailTool(ClaimflowTool):
    name: str = "metrics_fail_tool"
    description: str = "总是超时的测试工具"
    args_schema: type[_OkInput] = _OkInput

    def _run(self, *args: object, **kwargs: object) -> dict:
        raise NotImplementedError("仅支持异步调用")

    async def _arun(self, *, x: int = 1) -> dict:
        await asyncio.sleep(0.2)
        raise RuntimeError("boom")


def _guarded_with(
    tool: ClaimflowTool, *, timeout_s: float = 2.0, fallback: dict | None = None
) -> ClaimflowTool:
    """守卫装配（快速失败参数：无重试、1 次失败即熔断、短冷却）。"""
    return assemble_tool(
        tool,
        timeout_s=timeout_s,
        max_retries=0,
        backoff_initial=0.0,
        failure_threshold=1,
        breaker_cooldown=0.05,
        fallback=fallback,
        enable_cache=False,
    )


async def test_guard_success_metrics() -> None:
    guarded = _guarded_with(_OkTool(), timeout_s=2.0)
    await guarded.ainvoke({"x": 1})
    assert _counter_value(
        "claimflow_tool_calls_total", tool="metrics_ok_tool", status="success"
    ) >= 1.0


async def test_guard_error_metrics() -> None:
    guarded = _guarded_with(_FailTool(), timeout_s=0.05)
    try:
        await guarded.ainvoke({"x": 1})
    except Exception:  # noqa: BLE001 预期抛 ToolExecutionError
        pass
    assert _counter_value(
        "claimflow_tool_calls_total", tool="metrics_fail_tool", status="error"
    ) >= 1.0


async def test_guard_breaker_rejected_metrics() -> None:
    """熔断打开后：拒绝计数 + fallback 状态计数。"""
    guarded = _guarded_with(
        _FailTool(), timeout_s=0.05, fallback={"success": False, "error_message": "降级"}
    )
    # 第一次失败（超时）→ 返回降级结果，熔断打开（threshold=1）
    r1 = await guarded.ainvoke({"x": 1})
    assert r1["success"] is False and r1["error_message"] == "降级"
    assert _counter_value(
        "claimflow_tool_calls_total", tool="metrics_fail_tool", status="fallback"
    ) >= 1.0
    # 第二次被熔断器直接拒绝（同样降级）
    r2 = await guarded.ainvoke({"x": 1})
    assert r2["success"] is False
    assert _counter_value("claimflow_tool_breaker_rejected_total", tool="metrics_fail_tool") >= 1.0


# ===== observed_ainvoke：LLM 包装埋点 =====


class _FakeModel:
    """最小 LLM 假件：model_name + ainvoke 返回带 usage 的响应。"""

    model_name = "fake-llm"

    async def ainvoke(self, messages: Any, config: Any = None) -> Any:
        return type(
            "Resp", (), {"content": "ok", "usage_metadata": {"input_tokens": 7, "output_tokens": 3}}
        )()


class _ErrorModel:
    model_name = "fake-llm-error"

    async def ainvoke(self, messages: Any, config: Any = None) -> Any:
        raise RuntimeError("llm down")


class _DeepSeekCacheModel:
    """模拟 DeepSeek 非标字段路径：usage_metadata 丢弃缓存字段，
    原始值经 response_metadata.token_usage 存活（T162 探针实测口径）。"""

    model_name = "fake-deepseek-cache"

    async def ainvoke(self, messages: Any, config: Any = None) -> Any:
        return type(
            "Resp",
            (),
            {
                "content": "ok",
                "usage_metadata": {"input_tokens": 100, "output_tokens": 50},
                "response_metadata": {
                    "token_usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 50,
                        "prompt_cache_hit_tokens": 80,
                        "prompt_cache_miss_tokens": 20,
                    }
                },
            },
        )()


class _OpenAIStandardCacheModel:
    """模拟 langchain 标准字段回退路径：input_token_details.cache_read（换供应商通路）。"""

    model_name = "fake-openai-cache"

    async def ainvoke(self, messages: Any, config: Any = None) -> Any:
        return type(
            "Resp",
            (),
            {
                "content": "ok",
                "usage_metadata": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "input_token_details": {"cache_read": 64},
                },
                "response_metadata": {},
            },
        )()


async def test_observed_ainvoke_success() -> None:
    resp = await observed_ainvoke(_FakeModel(), [])  # type: ignore[arg-type]
    assert resp.content == "ok"
    assert _counter_value("claimflow_llm_calls_total", model="fake-llm", status="success") >= 1.0
    assert _counter_value("claimflow_llm_tokens_total", model="fake-llm", kind="prompt") >= 7.0
    assert _counter_value("claimflow_llm_tokens_total", model="fake-llm", kind="completion") >= 3.0


async def test_observed_ainvoke_deepseek_cache_tokens() -> None:
    """T162：DeepSeek 非标缓存字段从 response_metadata.token_usage 提取并埋点。"""
    await observed_ainvoke(_DeepSeekCacheModel(), [])  # type: ignore[arg-type]
    assert _counter_value(
        "claimflow_llm_cache_tokens_total",
        model="fake-deepseek-cache", stage="other", result="hit",
    ) >= 80.0
    assert _counter_value(
        "claimflow_llm_cache_tokens_total",
        model="fake-deepseek-cache", stage="other", result="miss",
    ) >= 20.0


async def test_observed_ainvoke_openai_standard_cache_fallback() -> None:
    """T162：无 DeepSeek 非标字段时回退 langchain 标准 cache_read（hit 有、miss 无）。"""
    await observed_ainvoke(_OpenAIStandardCacheModel(), [])  # type: ignore[arg-type]
    assert _counter_value(
        "claimflow_llm_cache_tokens_total",
        model="fake-openai-cache", stage="other", result="hit",
    ) >= 64.0
    assert (
        _counter_value(
            "claimflow_llm_cache_tokens_total", model="fake-openai-cache", stage="other", result="miss"
        )
        == 0.0
    )


async def test_observed_ainvoke_no_cache_fields() -> None:
    """T162：两路字段都没有（普通 fake）时不记缓存维度、不报错。"""
    await observed_ainvoke(_FakeModel(), [])  # type: ignore[arg-type]
    assert (
        _counter_value(
            "claimflow_llm_cache_tokens_total", model="fake-llm", stage="other", result="hit"
        )
        == 0.0
    )


async def test_observed_ainvoke_error_reraised() -> None:
    """LLM 异常原样抛出（节点降级逻辑依赖异常传播），同时记 error。"""
    raised = False
    try:
        await observed_ainvoke(_ErrorModel(), [])  # type: ignore[arg-type]
    except RuntimeError:
        raised = True
    assert raised
    assert _counter_value(
        "claimflow_llm_calls_total", model="fake-llm-error", status="error"
    ) >= 1.0


# ===== stage 维度与回调观测（T164）=====


async def test_observed_ainvoke_cache_stage_dimension() -> None:
    """T164：缓存指标按 stage 分列（current_phase 上下文携带，默认 other）。"""
    from services.observability.token_tracker import track_phase

    with track_phase("orchestrator"):
        await observed_ainvoke(_DeepSeekCacheModel(), [])  # type: ignore[arg-type]
    assert _counter_value(
        "claimflow_llm_cache_tokens_total",
        model="fake-deepseek-cache", stage="orchestrator", result="hit",
    ) >= 80.0


async def test_observed_ainvoke_accepts_structured_wrapper() -> None:
    """T164：model 为 with_structured_output 包装产物时按内层取模型名 + stage 仍生效。"""

    class _WrappedModel(_DeepSeekCacheModel):
        """模拟 RunnableSequence：.first 指回内层真实模型。"""

        @property
        def first(self) -> Any:
            return _DeepSeekCacheModel()

    from services.observability.token_tracker import track_phase

    with track_phase("material_review"):
        await observed_ainvoke(_WrappedModel(), [])  # type: ignore[arg-type]
    assert _counter_value(
        "claimflow_llm_cache_tokens_total",
        model="fake-deepseek-cache", stage="material_review", result="hit",
    ) >= 80.0


def test_model_name_recursive_probe() -> None:
    """T164：_model_name 沿 .bound / .first 递归探测内层模型名（结构化包装产物）。"""

    class _Inner:
        model_name = "deepseek-flash"

    class _Outer:
        def __init__(self) -> None:
            self.first = _Inner()

    from services.observability.llm_metrics import _model_name

    assert _model_name(_Inner()) == "deepseek-flash"
    assert _model_name(_Outer()) == "deepseek-flash"
    assert _model_name(object()) == "object"


def test_llm_usage_callback_handler_records_from_llm_output() -> None:
    """T164：回调 handler 从 on_llm_end 的 llm_output.token_usage 提取用量与缓存字段。

    create_agent 子图（worker_agent）内部 LLM 调用不走 observed_ainvoke，
    经 langchain 回调观测——该路径的存活字段是 llm_output（T162 探针实测口径）。
    """
    from langchain_core.outputs import LLMResult

    from services.observability.llm_metrics import LlmUsageCallbackHandler

    handler = LlmUsageCallbackHandler(model_name="deepseek-flash", stage="liability_judge")
    handler.on_llm_start({}, [])
    handler.on_llm_end(
        LLMResult(
            generations=[],
            llm_output={
                "token_usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 30,
                    "prompt_cache_hit_tokens": 96,
                    "prompt_cache_miss_tokens": 24,
                }
            },
        )
    )
    assert _counter_value(
        "claimflow_llm_cache_tokens_total",
        model="deepseek-flash", stage="liability_judge", result="hit",
    ) >= 96.0
    assert _counter_value(
        "claimflow_llm_cache_tokens_total",
        model="deepseek-flash", stage="liability_judge", result="miss",
    ) >= 24.0
    assert _counter_value(
        "claimflow_llm_tokens_total", model="deepseek-flash", kind="prompt"
    ) >= 120.0


def test_llm_usage_callback_handler_error_path() -> None:
    """T164：on_llm_error 记 error 状态，不记 token（无 usage）。"""
    from services.observability.llm_metrics import LlmUsageCallbackHandler

    handler = LlmUsageCallbackHandler(model_name="deepseek-flash", stage="liability_judge")
    handler.on_llm_start({}, [])
    handler.on_llm_error(RuntimeError("boom"))
    assert _counter_value(
        "claimflow_llm_calls_total", model="deepseek-flash", status="error"
    ) >= 1.0


# ===== /metrics 端点 =====


async def test_metrics_endpoint() -> None:
    """/metrics 返回 Prometheus 文本协议，含核心指标名。

    ASGITransport 不触发 lifespan（项目 API 测试惯例）——TestClient 的 with
    上下文会拉起完整启动流程含 BGE-M3 预下载，本地热缓存掩盖、CI 冷环境
    OSError 实锤（huggingface.co 不可达/2.3GB 下载不属于单测职责）。
    """
    from app.main import app

    metrics.record_tool_call("policy_query", "success", 0.1)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/metrics")

    assert resp.status_code == 200
    body = resp.text
    assert "claimflow_tool_calls_total" in body
    assert "claimflow_llm_calls_total" in body
    assert resp.headers["content-type"].startswith("text/plain")


def test_set_queue_depth_updates_gauge() -> None:
    """队列深度 Gauge（T154）：set_queue_depth 更新 queued/running 两列。"""
    from services.observability.metrics import (
        QUEUE_DEPTH,
        registry,
        set_queue_depth,
    )

    set_queue_depth(queued=3, running=1)

    def _value(status: str) -> float:
        for metric in registry.collect():
            if metric.name == "claimflow_case_jobs_queue_depth":
                for sample in metric.samples:
                    if sample.labels.get("status") == status:
                        return sample.value
        raise AssertionError("queue_depth 指标未注册")

    assert _value("queued") == 3
    assert _value("running") == 1
    assert QUEUE_DEPTH is not None


def test_set_queue_depth_swallows_errors(monkeypatch) -> None:
    """打点失败静默（不抛错不影响消费循环）。"""
    from services.observability import metrics as m

    def boom(*args, **kwargs):
        raise RuntimeError("registry down")

    monkeypatch.setattr(m.QUEUE_DEPTH, "labels", boom)
    m.set_queue_depth(queued=1, running=0)  # 不抛错即过
