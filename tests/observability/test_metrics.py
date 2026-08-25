"""T024 Prometheus 指标埋点测试：注册、打点维度、容错、/metrics 端点。"""

from __future__ import annotations

import time
from typing import Any

from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from pydantic import BaseModel

from schemas.tools import ToolOutput
from services.observability import metrics
from services.observability.llm_metrics import observed_ainvoke
from tools.base import BaseTool
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry


def _counter_value(name: str, **labels: str) -> float:
    """读全局 REGISTRY 中某 Counter 的当前值。"""
    return REGISTRY.get_sample_value(name, labels) or 0.0


# ===== metrics 模块：指标注册与打点函数 =====


def test_metrics_registered() -> None:
    """所有核心指标已在全局 REGISTRY 注册（/metrics 可暴露）。"""
    # 至少打一次点让带标签的样本出现
    metrics.record_tool_call("policy_query", "success", 0.1)
    metrics.record_breaker_rejected("ocr_extract")
    metrics.record_llm_call("deepseek-v4-flash", "success", 0.5, prompt_tokens=10, completion_tokens=5)
    metrics.record_turn("single_domain", 1.2, "PASS", False)

    assert _counter_value(
        "claimflow_tool_calls_total", tool="policy_query", status="success"
    ) >= 1.0
    assert _counter_value("claimflow_tool_breaker_rejected_total", tool="ocr_extract") >= 1.0
    assert _counter_value(
        "claimflow_llm_calls_total", model="deepseek-v4-flash", status="success"
    ) >= 1.0
    assert _counter_value(
        "claimflow_llm_tokens_total", model="deepseek-v4-flash", kind="prompt"
    ) >= 10.0
    assert _counter_value(
        "claimflow_llm_tokens_total", model="deepseek-v4-flash", kind="completion"
    ) >= 5.0
    assert _counter_value("claimflow_conversation_turns_total", intent="single_domain") >= 1.0
    assert _counter_value("claimflow_compliance_verdicts_total", verdict="PASS") >= 1.0
    assert _counter_value("claimflow_human_interventions_total") >= 0.0


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


def test_record_turn_human_intervention() -> None:
    """need_human=True 时转人工计数递增。"""
    before = _counter_value("claimflow_human_interventions_total")
    metrics.record_turn("multi_step", 5.0, "REJECTED", True)
    assert _counter_value("claimflow_human_interventions_total") == before + 1.0


# ===== ToolExecutor 集成：工具三态 + 熔断埋点 =====


class _OkInput(BaseModel):
    x: int = 1


class _OkOutput(ToolOutput):
    pass


class _OkTool(BaseTool):
    name = "metrics_ok_tool"
    description = "总是成功的测试工具"
    input_schema = _OkInput
    output_schema = _OkOutput

    async def _run(self, input_data: _OkInput) -> _OkOutput:
        return _OkOutput(success=True, data={"value": input_data.x})


class _FailTool(BaseTool):
    name = "metrics_fail_tool"
    description = "总是超时的测试工具"
    input_schema = _OkInput
    output_schema = _OkOutput

    async def _run(self, input_data: _OkInput) -> _OkOutput:
        time.sleep(0.2)
        raise RuntimeError("boom")


def _executor_with(tool: BaseTool, **kwargs: Any) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(tool)
    # 快速失败参数：无重试等待、极短超时、1 次失败即熔断、短冷却
    return ToolExecutor(
        registry,
        retry_backoff_base=0.0,
        max_retries=0,
        failure_threshold=1,
        breaker_cooldown=0.05,
        **kwargs,
    )


async def test_executor_success_metrics() -> None:
    executor = _executor_with(_OkTool(), default_timeout=2.0)
    await executor.execute("metrics_ok_tool", {"x": 1})
    assert _counter_value(
        "claimflow_tool_calls_total", tool="metrics_ok_tool", status="success"
    ) >= 1.0


async def test_executor_error_metrics() -> None:
    executor = _executor_with(_FailTool(), default_timeout=0.05)
    try:
        await executor.execute("metrics_fail_tool", {"x": 1})
    except Exception:  # noqa: BLE001 预期抛 ToolExecutionError
        pass
    assert _counter_value(
        "claimflow_tool_calls_total", tool="metrics_fail_tool", status="error"
    ) >= 1.0


async def test_executor_breaker_rejected_metrics() -> None:
    """熔断打开后：拒绝计数 + fallback 状态计数。"""
    executor = _executor_with(_FailTool(), default_timeout=0.05)
    fallback = ToolOutput(success=False, error_message="降级")
    # 第一次失败 → 熔断打开
    await executor.execute("metrics_fail_tool", {"x": 1}, fallback=fallback)
    assert _counter_value(
        "claimflow_tool_calls_total", tool="metrics_fail_tool", status="fallback"
    ) >= 1.0
    # 第二次被熔断器直接拒绝
    result = await executor.execute("metrics_fail_tool", {"x": 1}, fallback=fallback)
    assert result is fallback
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


async def test_observed_ainvoke_success() -> None:
    resp = await observed_ainvoke(_FakeModel(), [])  # type: ignore[arg-type]
    assert resp.content == "ok"
    assert _counter_value("claimflow_llm_calls_total", model="fake-llm", status="success") >= 1.0
    assert _counter_value("claimflow_llm_tokens_total", model="fake-llm", kind="prompt") >= 7.0
    assert _counter_value("claimflow_llm_tokens_total", model="fake-llm", kind="completion") >= 3.0


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


# ===== /metrics 端点 =====


def test_metrics_endpoint() -> None:
    """/metrics 返回 Prometheus 文本协议，含核心指标名。"""
    from app.main import app

    metrics.record_tool_call("policy_query", "success", 0.1)

    with TestClient(app) as client:
        resp = client.get("/metrics")

    assert resp.status_code == 200
    body = resp.text
    assert "claimflow_tool_calls_total" in body
    assert "claimflow_llm_calls_total" in body
    assert "claimflow_conversation_turns_total" in body
    assert "claimflow_compliance_verdicts_total" in body
    assert resp.headers["content-type"].startswith("text/plain")
