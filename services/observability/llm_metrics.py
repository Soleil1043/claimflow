"""LLM 调用观测包装（T024）：指标埋点 + 追踪 span 统一入口。

各节点的 `model.ainvoke(...)` 统一换成本模块 `observed_ainvoke(model, messages, ...)`：
- 成功/失败 + 耗时 → claimflow_llm_calls_total / claimflow_llm_latency_seconds
- usage_metadata（langchain 标准字段）→ claimflow_llm_tokens_total（prompt/completion 分维）
- response_metadata.token_usage 非标字段（T162）→ claimflow_llm_cache_tokens_total
  （DeepSeek Prompt Caching hit/miss；探针实测：langchain usage_metadata 丢弃非标字段，
  原始 usage dict 经 ainvoke 的 run manager 原样落进 message.response_metadata）

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


def _extract_cache_usage(response: Any) -> tuple[int | None, int | None]:
    """提取 Prompt Caching 用量（T162）→ (hit, miss)；取不到返回 (None, None)。

    两个来源（按优先级）：
    1. response_metadata["token_usage"]：OpenAI SDK 原始 usage dict——DeepSeek 非标字段
       prompt_cache_hit_tokens / prompt_cache_miss_tokens 在此存活（scripts/probe_deepseek_cache_usage.py
       探针实测：langchain usage_metadata 只映射标准字段丢弃非标字段，但 ainvoke 路径的
       run manager 会把 llm_output 原样写进 message.response_metadata）
    2. usage_metadata["input_token_details"]["cache_read"]：langchain 标准映射
       （OpenAI prompt_tokens_details.cached_tokens；DeepSeek 不返回该标准字段，
       留作切换供应商时的通路，miss 无对应标准字段只能留 None）
    """
    metadata = getattr(response, "response_metadata", None)
    token_usage = metadata.get("token_usage") if isinstance(metadata, dict) else None
    if isinstance(token_usage, dict):
        hit = token_usage.get("prompt_cache_hit_tokens")
        miss = token_usage.get("prompt_cache_miss_tokens")
        if isinstance(hit, int) or isinstance(miss, int):
            return (hit if isinstance(hit, int) else None, miss if isinstance(miss, int) else None)
    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict):
        details = usage.get("input_token_details")
        if isinstance(details, dict):
            cache_read = details.get("cache_read")
            if isinstance(cache_read, int):
                return cache_read, None
    return None, None


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
        cache_hit_tokens, cache_miss_tokens = _extract_cache_usage(response)
        metrics.record_llm_call(
            name,
            "success",
            time.perf_counter() - started,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
        )
        if prompt_tokens is not None:
            span.set_attribute("gen_ai.usage.input_tokens", prompt_tokens)
        if completion_tokens is not None:
            span.set_attribute("gen_ai.usage.output_tokens", completion_tokens)
        if cache_hit_tokens is not None:
            span.set_attribute("gen_ai.usage.cache_read_tokens", cache_hit_tokens)
        if cache_miss_tokens is not None:
            span.set_attribute("gen_ai.usage.cache_miss_tokens", cache_miss_tokens)
        # T029：归集到当前轮次的 token tracker（contextvars，无请求上下文时无操作）
        if prompt_tokens is not None and completion_tokens is not None:
            from services.observability.token_tracker import record_usage_to_tracker

            record_usage_to_tracker(name, prompt_tokens, completion_tokens)
        return response


