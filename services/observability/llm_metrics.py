"""LLM 调用观测包装（T024，architecture.md 8.1 LLM 指标）。

各节点的 `model.ainvoke(...)` 统一换成本模块 `observed_ainvoke(model, messages, ...)`：
- 成功/失败 + 耗时 → claimflow_llm_calls_total / claimflow_llm_latency_seconds
- usage_metadata（langchain 标准字段）→ claimflow_llm_tokens_total（prompt/completion 分维）

为什么不在 client.py 埋：ChatOpenAI 是 langchain 单例，没有统一的请求钩子；
在调用侧包一层是最小侵入方案（各节点一行替换）。
"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AnyMessage

from app.core.logging import get_logger
from services.observability import metrics

log = get_logger(__name__)


def _model_name(model: BaseChatModel) -> str:
    """模型名（标签用）；取不到时用类名兜底。"""
    return (
        getattr(model, "model_name", None) or getattr(model, "model", None) or type(model).__name__
    )


def _extract_usage(response: Any) -> tuple[int | None, int | None]:
    """提取 token 用量：langchain AIMessage.usage_metadata → (prompt, completion)。"""
    usage = getattr(response, "usage_metadata", None)
    if not isinstance(usage, dict):
        return None, None
    return usage.get("input_tokens"), usage.get("output_tokens")


async def observed_ainvoke(
    model: BaseChatModel,
    messages: list[AnyMessage],
    *,
    config: dict[str, Any] | None = None,
) -> Any:
    """带指标埋点 + 追踪 span 的 ainvoke：LLM 异常原样抛出（由各节点既有降级逻辑处理）。

    T039：LLM span 一处埋点覆盖全部调用点（phase_ainvoke 传递环节上下文）；
    属性含模型 / 环节 / token 用量；OTel 未启用时为 noop span，零开销。
    """
    from services.observability.token_tracker import current_phase
    from services.observability.tracing import ATTR_PHASE, traced_span

    name = _model_name(model)
    started = time.perf_counter()
    with traced_span(
        f"llm.{name}", **{"gen_ai.request.model": name, ATTR_PHASE: current_phase()}
    ) as span:
        try:
            response = await model.ainvoke(messages, config=config)
        except Exception:
            metrics.record_llm_call(name, "error", time.perf_counter() - started)
            raise
        prompt_tokens, completion_tokens = _extract_usage(response)
        metrics.record_llm_call(
            name,
            "success",
            time.perf_counter() - started,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        if prompt_tokens is not None:
            span.set_attribute("gen_ai.usage.input_tokens", prompt_tokens)
        if completion_tokens is not None:
            span.set_attribute("gen_ai.usage.output_tokens", completion_tokens)
        # T029：归集到当前轮次的 token tracker（contextvars，无请求上下文时无操作）
        if prompt_tokens is not None and completion_tokens is not None:
            from services.observability.token_tracker import record_usage_to_tracker

            record_usage_to_tracker(name, prompt_tokens, completion_tokens)
        return response
