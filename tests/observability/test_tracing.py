"""OTel 追踪埋点测试（T039）。

用 InMemorySpanExporter + 测试 TracerProvider（monkeypatch tracing.get_tracer，
不污染全局 provider），验证 LLM / 工具 / 合规裁决 span 的名称与属性。
OTel 未启用时全部埋点为 noop span——回归由现有 346 用例保证。
"""

from __future__ import annotations

from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import services.observability.tracing as tracing_module
from schemas.tools import ToolInput, ToolOutput
from services.observability.tracing import ATTR_PHASE, ATTR_TOOL_NAME
from tools.base import BaseTool
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry


class _FakeResp:
    content = "ok"

    usage_metadata = {"input_tokens": 12, "output_tokens": 34}


class _FakeModel:
    model_name = "test-model"

    async def ainvoke(self, messages: Any, config: Any = None) -> _FakeResp:
        return _FakeResp()


class _EchoInput(ToolInput):
    text: str


class _EchoTool(BaseTool):
    name = "echo_tool"
    description = "测试用回显工具"

    input_schema = _EchoInput
    output_schema = ToolOutput

    async def _run(self, input_data: _EchoInput) -> ToolOutput:
        return ToolOutput(success=True, data={"echo": input_data.text})


@pytest.fixture()
def exporter(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    """测试 tracer + 内存 exporter（测试内自行 get_finished_spans 取实时快照）。"""
    mem = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(mem))
    monkeypatch.setattr(tracing_module, "get_tracer", lambda: provider.get_tracer("test"))
    return mem


def _spans(exporter: InMemorySpanExporter) -> list:
    return exporter.get_finished_spans()


async def test_llm_span_attributes(exporter: InMemorySpanExporter) -> None:
    """LLM span：名称/模型/环节/token 属性（observed_ainvoke 一处埋点）。"""
    from services.observability.llm_metrics import observed_ainvoke
    from services.observability.token_tracker import track_phase

    with track_phase("intent"):
        await observed_ainvoke(_FakeModel(), [])  # type: ignore[arg-type]

    spans = _spans(exporter)
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "llm.test-model"
    attrs = span.attributes
    assert attrs["gen_ai.request.model"] == "test-model"
    assert attrs[ATTR_PHASE] == "intent"
    assert attrs["gen_ai.usage.input_tokens"] == 12
    assert attrs["gen_ai.usage.output_tokens"] == 34


async def test_tool_span_attributes(exporter: InMemorySpanExporter) -> None:
    """工具 span：名称与工具名属性（ToolExecutor 公共入口包装）。"""
    registry = ToolRegistry()
    registry.register(_EchoTool())
    executor = ToolExecutor(registry)

    result = await executor.execute("echo_tool", {"text": "hi"})
    assert result.success is True
    spans = _spans(exporter)
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "tool.echo_tool"
    assert span.attributes[ATTR_TOOL_NAME] == "echo_tool"


async def test_compliance_span_attributes(
    exporter: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """合规裁决 span：verdict / risk_score 属性（ComplianceNode 内）。"""
    import nodes.compliance as compliance_module
    from schemas.agent_outputs import ComplianceAgentOutput

    async def fake_review(text: str, executor: Any = None) -> ComplianceAgentOutput:
        return ComplianceAgentOutput(verdict="REJECT", violations=[], risk_score=98, reason="测试")

    monkeypatch.setattr(compliance_module, "review_answer", fake_review)

    update = await compliance_module.ComplianceNode(executor=None)({"final_answer": "测试草稿"})
    assert update["compliance_result"]["verdict"] == "REJECT"
    spans = _spans(exporter)
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert spans[0].name == "compliance.review"
    assert attrs[tracing_module.ATTR_COMPLIANCE_VERDICT] == "REJECT"
    assert attrs[tracing_module.ATTR_COMPLIANCE_RISK] == 98


def test_span_hierarchy_parent_child(exporter: InMemorySpanExporter) -> None:
    """父子关系：嵌套 span 共享 trace_id 且父正确（调用树结构基础）。"""
    with tracing_module.traced_span("parent"):
        with tracing_module.traced_span("child"):
            pass
    spans = _spans(exporter)
    assert len(spans) == 2
    child, parent = spans[0], spans[1]
    assert child.context.trace_id == parent.context.trace_id
    assert child.parent is not None
    assert child.parent.span_id == parent.context.span_id


def test_traced_span_skips_none_attributes(exporter: InMemorySpanExporter) -> None:
    """None 属性自动跳过（半初始化字段不污染 span）。"""
    with tracing_module.traced_span("op", maybe_none=None, real="x"):
        pass
    spans = _spans(exporter)
    assert spans[0].attributes == {"real": "x"}


def test_setup_disabled_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """开关关闭：setup 返回 False 不初始化（全部埋点走 noop tracer）。"""
    monkeypatch.setattr(tracing_module.settings, "otel_enabled", False)
    tracing_module.reset_tracing()
    assert tracing_module.setup_tracing() is False
    assert tracing_module.setup_tracing() is False  # 幂等
