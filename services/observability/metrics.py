"""Prometheus 指标定义与埋点辅助。

三类指标：
- 工具指标：调用成功率（Counter 按 status 分维）、耗时直方图、熔断拒绝计数
- LLM 指标：调用耗时、Token 消耗（prompt/completion 分维）
- 业务指标：轮次总数、转人工、合规三态、端到端处理时长直方图
- 核赔指标（Phase 8 T090）：案件总量/自动签发率/转人工率/阶段耗时/调度调用/
  守卫纠错/兜底触发/补件轮次/核定金额分布

约定：
- 指标在模块导入时注册（进程级单例 REGISTRY），多事件循环共享安全
- 所有打点函数容忍指标缺失（测试隔离场景），不因观测失败中断业务
"""

from __future__ import annotations

from typing import Any

from prometheus_client import REGISTRY, Counter, Gauge, Histogram
from prometheus_client.core import REGISTRY as _GLOBAL_REGISTRY

# 统一使用全局默认 REGISTRY：prometheus-fastapi-instrumentator / make_asgi_app 均读取它
registry = _GLOBAL_REGISTRY

# ===== 工具指标 =====

TOOL_CALLS = Counter(
    "claimflow_tool_calls_total",
    "工具调用总次数",
    labelnames=["tool", "status"],  # status: success | fallback | error
    registry=registry,
)

TOOL_LATENCY = Histogram(
    "claimflow_tool_latency_seconds",
    "工具执行耗时（秒）",
    labelnames=["tool"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=registry,
)

TOOL_BREAKER_REJECTED = Counter(
    "claimflow_tool_breaker_rejected_total",
    "工具调用被熔断器拒绝次数",
    labelnames=["tool"],
    registry=registry,
)

TOOL_CACHE_HITS = Counter(
    "claimflow_tool_cache_hits_total",
    "工具结果缓存命中次数（T028）",
    labelnames=["tool", "result"],  # result: hit | miss | disabled
    registry=registry,
)

# ===== LLM 指标 =====

LLM_CALLS = Counter(
    "claimflow_llm_calls_total",
    "LLM 调用总次数",
    labelnames=["model", "status"],  # status: success | error
    registry=registry,
)

LLM_LATENCY = Histogram(
    "claimflow_llm_latency_seconds",
    "LLM 调用耗时（秒）",
    labelnames=["model"],
    buckets=(0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 60.0),
    registry=registry,
)

LLM_TOKENS = Counter(
    "claimflow_llm_tokens_total",
    "LLM Token 消耗",
    labelnames=["model", "kind"],  # kind: prompt | completion
    registry=registry,
)

# Prompt Caching 命中观测（T162）：DeepSeek 硬盘缓存默认生效（命中价 1/10，无需改代码），
# 但 langchain usage_metadata 丢弃非标字段，原始值由 llm_metrics 从
# response_metadata.token_usage 提取后传入；命中率 = hit / (hit + miss)（PromQL 派生）
LLM_CACHE_TOKENS = Counter(
    "claimflow_llm_cache_tokens_total",
    "LLM 输入 Prompt Caching 命中/未命中 token 数（DeepSeek 硬盘缓存，T162）",
    labelnames=["model", "result"],  # result: hit | miss
    registry=registry,
)

# ===== 核赔业务指标（Phase 8 T090，D037/D039） =====

CASES_TOTAL = Counter(
    "claimflow_cases_total",
    "核赔案件总数（按险种+终态分维——auto_close_rate/referral_rate 从此推导）",
    labelnames=["case_type", "final_status"],  # final_status: auto_issued | referred | closed
    registry=registry,
)

CASE_STAGE_LATENCY = Histogram(
    "claimflow_case_stage_seconds",
    "案件各阶段耗时（秒）",
    labelnames=["stage"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=registry,
)

CASE_DURATION = Histogram(
    "claimflow_case_duration_seconds",
    "案件端到端处理时长（秒，提交→签发/转人工）",
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
    registry=registry,
)

ROUTING_CALLS = Histogram(
    "claimflow_routing_calls_per_case",
    "每案件 orchestrator 调度调用次数（预算 ≤15，D039 防绕圈）",
    buckets=(1, 3, 5, 8, 10, 12, 15, 20),
    registry=registry,
)

GUARD_CORRECTIONS = Counter(
    "claimflow_guard_corrections_total",
    "orchestrator 前置条件守卫纠错次数（D039 安全设计 2）",
    registry=registry,
)

ORCH_FALLBACK = Counter(
    "claimflow_orchestrator_fallback_total",
    "orchestrator LLM 失败回退确定性兜底次数（D039 安全设计 3）",
    registry=registry,
)

SUPPLEMENT_ROUNDS = Histogram(
    "claimflow_supplement_rounds",
    "补件轮次分布（每案件补件请求次数）",
    buckets=(1, 2, 3, 5),
    registry=registry,
)

DECISION_AMOUNT = Histogram(
    "claimflow_decision_amount",
    "核定金额分布（元；rejected 案为 0）",
    buckets=(0, 100, 500, 1000, 3000, 5000, 10000, 50000, 100000, 1000000),
    registry=registry,
)

CASE_TOKENS = Counter(
    "claimflow_case_tokens_total",
    "核赔案件 LLM token 消耗（T090）",
    labelnames=["model"],
    registry=registry,
)

# 交付队列深度（T154 告警信号）：JobLoop 每 tick 刷新（queued/running 分列）
QUEUE_DEPTH = Gauge(
    "claimflow_case_jobs_queue_depth",
    "交付队列深度（按任务状态分列；queued 持续高位 = 积压告警信号，T154）",
    labelnames=["status"],
    registry=registry,
)


def set_queue_depth(queued: int, running: int) -> None:
    """刷新队列深度 Gauge（JobLoop 每 tick 调用；打点失败不影响消费循环）。"""
    try:
        QUEUE_DEPTH.labels(status="queued").set(queued)
        QUEUE_DEPTH.labels(status="running").set(running)
    except Exception:  # noqa: BLE001 埋点容错
        pass


def _safe_inc(counter: Counter | Gauge | None, amount: float = 1.0, **labels: Any) -> None:
    """打点失败不抛错：观测层异常不允许影响业务链路。"""
    if counter is None:
        return
    try:
        if labels:
            counter.labels(**labels).inc(amount)
        else:
            counter.inc(amount)
    except Exception:  # noqa: BLE001 埋点容错
        pass


def _safe_observe(histogram: Histogram | None, value: float, **labels: str) -> None:
    if histogram is None:
        return
    try:
        # 无标签直方图调用 .labels() 会抛 ValueError 被吞——观测静默丢失
        # （T103 测试逮到：CASE_DURATION/ROUTING_CALLS 自 T090 起从未记过数）
        (histogram.labels(**labels) if labels else histogram).observe(value)
    except Exception:  # noqa: BLE001 埋点容错
        pass


def record_tool_call(tool: str, status: str, duration_s: float) -> None:
    """工具调用结果埋点：success / fallback / error 三态计数 + 耗时。"""
    _safe_inc(TOOL_CALLS, tool=tool, status=status)
    _safe_observe(TOOL_LATENCY, duration_s, tool=tool)


def record_breaker_rejected(tool: str) -> None:
    """熔断器拒绝埋点（此时无真实执行，不记耗时）。"""
    _safe_inc(TOOL_BREAKER_REJECTED, tool=tool)


def record_tool_cache(tool: str, result: str) -> None:
    """工具缓存结果埋点：hit / miss / disabled（T028）。"""
    _safe_inc(TOOL_CACHE_HITS, tool=tool, result=result)


def record_llm_call(
    model: str,
    status: str,
    duration_s: float,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    cache_hit_tokens: int | None = None,
    cache_miss_tokens: int | None = None,
) -> None:
    """LLM 调用结果埋点：成功/失败 + 耗时 + Token 用量（usage 缺失时不记 token）。

    cache_hit_tokens / cache_miss_tokens（T162）：DeepSeek Prompt Caching 口径，
    调用方从 response_metadata.token_usage 提取非标字段传入；None（模型不返回
    或提取失败）时不记缓存维度——不臆造 0，宁缺勿假。
    """
    _safe_inc(LLM_CALLS, model=model, status=status)
    _safe_observe(LLM_LATENCY, duration_s, model=model)
    if prompt_tokens is not None:
        _safe_inc(LLM_TOKENS, model=model, kind="prompt", amount=float(prompt_tokens))
    if completion_tokens is not None:
        _safe_inc(LLM_TOKENS, model=model, kind="completion", amount=float(completion_tokens))
    if cache_hit_tokens is not None:
        _safe_inc(LLM_CACHE_TOKENS, model=model, result="hit", amount=float(cache_hit_tokens))
    if cache_miss_tokens is not None:
        _safe_inc(LLM_CACHE_TOKENS, model=model, result="miss", amount=float(cache_miss_tokens))


# ---------- 核赔埋点辅助（T090） ----------


def record_case_closed(case_type: str, final_status: str) -> None:
    """案件终态埋点（auto_issued / referred / closed——auto_close_rate 与 referral_rate 分母）。"""
    _safe_inc(CASES_TOTAL, case_type=case_type, final_status=final_status)


def record_case_stage(stage: str, duration_s: float) -> None:
    """案件阶段耗时埋点。"""
    _safe_observe(CASE_STAGE_LATENCY, duration_s, stage=stage)


def record_case_duration(duration_s: float) -> None:
    """案件端到端耗时埋点。"""
    _safe_observe(CASE_DURATION, duration_s)


def record_routing_calls(calls: int) -> None:
    """调度调用次数埋点（预算监控）。"""
    _safe_observe(ROUTING_CALLS, calls)


def record_guard_correction() -> None:
    """守卫纠错埋点。"""
    _safe_inc(GUARD_CORRECTIONS)


def record_orch_fallback() -> None:
    """LLM 兜底触发埋点。"""
    _safe_inc(ORCH_FALLBACK)


def record_supplement_rounds(rounds: int) -> None:
    """补件轮次埋点。"""
    _safe_observe(SUPPLEMENT_ROUNDS, rounds)


def record_decision_amount(amount: float) -> None:
    """核定金额分布埋点。"""
    _safe_observe(DECISION_AMOUNT, amount)


def record_case_tokens(model: str, tokens: int) -> None:
    """案件 token 消耗埋点。"""
    _safe_inc(CASE_TOKENS, model=model, amount=float(tokens))


__all__ = [
    "CASES_TOTAL",
    "CASE_DURATION",
    "CASE_STAGE_LATENCY",
    "CASE_TOKENS",
    "DECISION_AMOUNT",
    "GUARD_CORRECTIONS",
    "LLM_CALLS",
    "LLM_CACHE_TOKENS",
    "LLM_LATENCY",
    "LLM_TOKENS",
    "ORCH_FALLBACK",
    "REGISTRY",
    "ROUTING_CALLS",
    "SUPPLEMENT_ROUNDS",
    "TOOL_BREAKER_REJECTED",
    "TOOL_CACHE_HITS",
    "TOOL_CALLS",
    "TOOL_LATENCY",
    "record_breaker_rejected",
    "record_case_closed",
    "record_case_duration",
    "record_case_stage",
    "record_case_tokens",
    "record_decision_amount",
    "record_guard_correction",
    "record_llm_call",
    "record_orch_fallback",
    "record_routing_calls",
    "record_supplement_rounds",
    "record_tool_cache",
    "record_tool_call",
]
