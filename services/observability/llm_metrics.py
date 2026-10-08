"""LLM 调用观测包装（T024）：指标埋点 + 追踪 span 统一入口。

各节点的 `model.ainvoke(...)` 统一换成本模块 `observed_ainvoke(model, messages, ...)`：
- 成功/失败 + 耗时 → claimflow_llm_calls_total / claimflow_llm_latency_seconds
- usage_metadata（langchain 标准字段）→ claimflow_llm_tokens_total（prompt/completion 分维）
- response_metadata.token_usage 非标字段（T162）→ claimflow_llm_cache_tokens_total
  （DeepSeek Prompt Caching hit/miss；探针实测：langchain usage_metadata 丢弃非标字段，
  原始 usage dict 经 ainvoke 的 run manager 原样落进 message.response_metadata）
- stage 维度（T164）：缓存指标按调用点分列（current_phase 上下文自动携带；
  orchestrator / material_review / decision_writer / liability_judge …）——
  材料提取（ocr）prompt 主体即唯一文档内容属天然 miss，布局收益评估须分调用点看。

为什么不在 client.py 埋：ChatOpenAI 是 langchain 单例，没有统一的请求钩子；
在调用侧包一层是最小侵入方案（各节点一行替换）。

create_agent 子图（worker_agent）不经过本入口——其内部 LLM 调用经
LlmUsageCallbackHandler 回调观测（on_llm_end 的 llm_output.token_usage，
T162 探针验证过的存活路径）。
"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AnyMessage
from langchain_core.outputs import LLMResult
from langchain_core.runnables import Runnable

from app.core.logging import get_logger
from services.observability import metrics

log = get_logger(__name__)


def _model_name(model: Any) -> str:
    """模型名（指标标签用）；包装 Runnable（with_structured_output 产物）递归探测内层。

    探测链：model_name / model 直取 → .bound / .first 内层递归（RunnableBinding、
    RunnableSequence 常见形态）→ 类名兜底。
    """
    for attr in ("model_name", "model"):
        v = getattr(model, attr, None)
        if isinstance(v, str) and v:
            return v
    for inner_attr in ("bound", "first"):
        inner = getattr(model, inner_attr, None)
        if inner is not None and inner is not model:
            name = _model_name(inner)
            if name != type(inner).__name__:
                return name
    return type(model).__name__


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
    model: Runnable | BaseChatModel,
    messages: list[AnyMessage],
    *,
    config: dict[str, Any] | None = None,
    model_name: str | None = None,
) -> Any:
    """带指标埋点 + 追踪 span 的 ainvoke：LLM 异常原样抛出（由各节点既有降级逻辑处理）。

    T039：LLM span 一处埋点覆盖全部调用点（phase_ainvoke 传递环节上下文）；
    属性含模型 / 环节 / token 用量；OTel 未启用时为 noop span，零开销。
    T164 泛化：model 接受 with_structured_output 包装后的 Runnable
    （_model_name 递归探测内层模型名，或显式传 model_name 覆盖）。

    结构化输出的用量提取走回调分支：with_structured_output 返回纯 Pydantic 对象，
    usage_metadata / response_metadata 均不存在（实测），只能从
    LlmUsageCallbackHandler.on_llm_end 的 llm_output 取——此时本函数只负责
    span 与异常埋点，用量由 handler 记，避免同一次调用重复计数。
    """
    from services.observability.token_tracker import current_phase
    from services.observability.tracing import ATTR_PHASE, traced_span

    name = model_name or _model_name(model)
    stage = current_phase()
    # 结构化输出判据：返回对象不带 usage metadata 的包装 Runnable。不能用
    # isinstance(BaseChatModel)——测试替身与with_structured_output 产物同为
    # 非 BaseChatModel 但前者直接返回 AIMessage；判据取「是否自带模型身份属性」。
    structured = not hasattr(model, "model_name") and not hasattr(model, "model")
    started = time.perf_counter()
    cfg = dict(config or {})
    handler: LlmUsageCallbackHandler | None = None
    if structured:
        # 结构化输出：usage 只能经回调拿（返回对象不带 metadata）
        handler = LlmUsageCallbackHandler(model_name=name, stage=stage)
        existing = cfg.get("callbacks")
        cfg["callbacks"] = [*existing, handler] if existing else [handler]
    with traced_span(
        f"llm.{name}", **{"gen_ai.request.model": name, ATTR_PHASE: stage}
    ) as span:
        try:
            response = await model.ainvoke(messages, config=cfg)
        except Exception:
            metrics.record_llm_call(name, "error", time.perf_counter() - started, stage=stage)
            raise
        if structured:
            # 用量已由 handler 记（on_llm_end）；此处仅补 span 的 token 属性
            if handler is not None:
                for key, attr in (
                    ("prompt_tokens", "gen_ai.usage.input_tokens"),
                    ("completion_tokens", "gen_ai.usage.output_tokens"),
                    ("cache_hit_tokens", "gen_ai.usage.cache_read_tokens"),
                    ("cache_miss_tokens", "gen_ai.usage.cache_miss_tokens"),
                ):
                    value = getattr(handler, key, None)
                    if value is not None:
                        span.set_attribute(attr, value)
            return response
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
            stage=stage,
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


class LlmUsageCallbackHandler(BaseCallbackHandler):
    """create_agent 子图的 LLM 用量回调观测（T164）。

    worker_agent 装配的是 langgraph CompiledStateGraph，其内部 chat model 调用
    不经 observed_ainvoke；langgraph 会把 config.callbacks 传播到节点内的模型调用，
    on_llm_end 收到的 LLMResult.llm_output["token_usage"] 是 OpenAI SDK 原始
    usage dict（T162 探针实测：DeepSeek 非标缓存字段在此存活）。

    用法：worker.ainvoke(payload, config={"callbacks": [LlmUsageCallbackHandler(
        model_name=settings.llm_model, stage="liability_judge")]})
    一个 handler 实例跨子图内多轮 LLM 调用（ReAct 循环）依次触发，串行安全。
    """

    def __init__(self, model_name: str, stage: str = "other") -> None:
        self._model_name = model_name
        self._stage = stage
        self._started = 0.0
        # 最近一次调用的用量（observed_ainvoke 结构化分支读它补 span 属性）
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self.cache_hit_tokens: int | None = None
        self.cache_miss_tokens: int | None = None

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any) -> None:
        self._started = time.perf_counter()
        self.prompt_tokens = self.completion_tokens = None
        self.cache_hit_tokens = self.cache_miss_tokens = None

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        duration = time.perf_counter() - self._started if self._started else 0.0
        usage = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        hit = usage.get("prompt_cache_hit_tokens")
        miss = usage.get("prompt_cache_miss_tokens")
        self.prompt_tokens = prompt_tokens if isinstance(prompt_tokens, int) else None
        self.completion_tokens = completion_tokens if isinstance(completion_tokens, int) else None
        self.cache_hit_tokens = hit if isinstance(hit, int) else None
        self.cache_miss_tokens = miss if isinstance(miss, int) else None
        metrics.record_llm_call(
            self._model_name,
            "success",
            duration,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            cache_hit_tokens=self.cache_hit_tokens,
            cache_miss_tokens=self.cache_miss_tokens,
            stage=self._stage,
        )

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        duration = time.perf_counter() - self._started if self._started else 0.0
        metrics.record_llm_call(self._model_name, "error", duration, stage=self._stage)
