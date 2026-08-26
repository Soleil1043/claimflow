"""OpenTelemetry 全链路追踪（T039，architecture.md 8.2，D015 后置项落地）。

埋点结构（trace_id 贯穿 A06 → 节点 → LLM/工具）：
- FastAPI instrumentation：A06 请求级 server span（入口）
- LLM 调用 span：services/observability/llm_metrics.observed_ainvoke 统一包装——
  一处埋点覆盖全部调用点（intent/planner/generator/compliance/runner/ocr/memory），
  属性含模型名、环节（phase）、token 用量（usage 提取处天然可得）
- 工具调用 span：ToolExecutor.execute 统一包装，属性含工具名与成败
- 合规裁决：ComplianceNode 内 span 属性 verdict / risk_score / rounds

部署：compose `--profile tracing` 起 OTel Collector（4317）+ Jaeger（16686 UI）；
采样率/开关/endpoint 配置化（OTEL_ENABLED / OTEL_ENDPOINT / OTEL_SAMPLER_RATIO）。
未启用时 setup 直接跳过，OTel API 层 tracer 为 noop——零开销零侵入。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# span 属性名统一收敛（claimflow.* 命名空间，避免与语义约定冲突）
ATTR_PHASE = "claimflow.phase"
ATTR_TOOL_NAME = "claimflow.tool.name"
ATTR_COMPLIANCE_VERDICT = "claimflow.compliance.verdict"
ATTR_COMPLIANCE_RISK = "claimflow.compliance.risk_score"

_setup_done = False


def setup_tracing(app: Any = None) -> bool:
    """初始化 OTel（幂等）：TracerProvider + OTLP exporter + FastAPI instrumentation。

    Returns: 是否实际启用（开关关闭返回 False，全部埋点走 noop tracer）。
    """
    global _setup_done
    if _setup_done or not settings.otel_enabled:
        return False

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    provider = TracerProvider(
        resource=Resource.create({"service.name": "claimflow"}),
        sampler=ParentBased(TraceIdRatioBased(settings.otel_sampling_ratio)),
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint, insecure=True))
    )
    trace.set_tracer_provider(provider)

    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)

    _setup_done = True
    log.info(
        "otel_tracing_enabled",
        endpoint=settings.otel_endpoint,
        sampling_ratio=settings.otel_sampling_ratio,
    )
    return True


def get_tracer() -> Any:
    """当前 tracer；未 setup 时 OTel API 返回 noop tracer（span 零开销）。"""
    from opentelemetry import trace

    return trace.get_tracer("claimflow")


@contextmanager
def traced_span(name: str, **attributes: Any) -> Iterator[Any]:
    """便捷 span：`with traced_span("llm.call", model=..., phase=...):`。

    None 值属性自动跳过（避免半初始化字段污染）；内部异常不标记 span error，
    由调用方按需 set_status（观测路径不影响业务）。
    """
    with get_tracer().start_as_current_span(name) as span:
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        yield span


def reset_tracing() -> None:
    """复位初始化标记（测试用；全局 TracerProvider 进程内只能设置一次）。"""
    global _setup_done
    _setup_done = False
