"""Prometheus 指标定义与埋点辅助（architecture.md 8.1/8.2）。

三类指标：
- 工具指标：调用成功率（Counter 按 status 分维）、耗时直方图、熔断拒绝计数
- LLM 指标：调用耗时、Token 消耗（prompt/completion 分维）
- 业务指标：轮次总数、转人工、合规三态、端到端处理时长直方图

约定：
- 指标在模块导入时注册（进程级单例 REGISTRY），多事件循环共享安全
- 所有打点函数容忍指标缺失（测试隔离场景），不因观测失败中断业务
"""

from __future__ import annotations

from typing import Any

from prometheus_client import REGISTRY, Counter, Histogram
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

TURN_TOKENS = Counter(
    "claimflow_turn_tokens_total",
    "单轮对话 token 消耗（按环节分维，T029）",
    labelnames=[
        "phase",
        "model",
    ],  # phase: intent | planner | executor | generator | compliance | other
    registry=registry,
)

# ===== 业务指标 =====

CONVERSATION_TURNS = Counter(
    "claimflow_conversation_turns_total",
    "对话轮次总数",
    labelnames=["intent"],
    registry=registry,
)

HUMAN_INTERVENTIONS = Counter(
    "claimflow_human_interventions_total",
    "转人工轮次数",
    registry=registry,
)

COMPLIANCE_VERDICTS = Counter(
    "claimflow_compliance_verdicts_total",
    "合规审查三态计数",
    labelnames=["verdict"],  # PASS | MODIFIED | REJECTED | NONE
    registry=registry,
)

TURN_LATENCY = Histogram(
    "claimflow_turn_latency_seconds",
    "单轮对话端到端处理时长（秒，A06 收到请求 → 返回回答）",
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0),
    registry=registry,
)

MEMORY_WRITES = Counter(
    "claimflow_memory_writes_total",
    "长期记忆写入次数（旁路路径，失败不阻断对话）",
    labelnames=["result"],  # success | error
    registry=registry,
)


def _safe_inc(counter: Counter | None, amount: float = 1.0, **labels: Any) -> None:
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
        histogram.labels(**labels).observe(value)
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


def record_turn_tokens(phase: str, model: str, tokens: int) -> None:
    """单轮对话分环节 token 埋点（T029）。"""
    _safe_inc(TURN_TOKENS, phase=phase, model=model, amount=float(tokens))


def record_llm_call(
    model: str,
    status: str,
    duration_s: float,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> None:
    """LLM 调用结果埋点：成功/失败 + 耗时 + Token 用量（usage 缺失时不记 token）。"""
    _safe_inc(LLM_CALLS, model=model, status=status)
    _safe_observe(LLM_LATENCY, duration_s, model=model)
    if prompt_tokens is not None:
        _safe_inc(LLM_TOKENS, model=model, kind="prompt", amount=float(prompt_tokens))
    if completion_tokens is not None:
        _safe_inc(LLM_TOKENS, model=model, kind="completion", amount=float(completion_tokens))


def record_turn(
    intent: str,
    duration_s: float,
    compliance_verdict: str,
    need_human: bool,
) -> None:
    """一轮对话结束埋点：意图、端到端耗时、合规三态、转人工。"""
    _safe_inc(CONVERSATION_TURNS, intent=intent)
    _safe_observe(TURN_LATENCY, duration_s)
    _safe_inc(COMPLIANCE_VERDICTS, verdict=compliance_verdict)
    if need_human:
        _safe_inc(HUMAN_INTERVENTIONS)


def record_memory_write(result: str) -> None:
    """长期记忆写入结果埋点（T034 旁路路径，失败静默只计数）。"""
    _safe_inc(MEMORY_WRITES, result=result)


__all__ = [
    "COMPLIANCE_VERDICTS",
    "CONVERSATION_TURNS",
    "HUMAN_INTERVENTIONS",
    "LLM_CALLS",
    "LLM_LATENCY",
    "LLM_TOKENS",
    "MEMORY_WRITES",
    "TOOL_BREAKER_REJECTED",
    "TOOL_CACHE_HITS",
    "TOOL_CALLS",
    "TOOL_LATENCY",
    "TURN_LATENCY",
    "TURN_TOKENS",
    "REGISTRY",
    "record_breaker_rejected",
    "record_llm_call",
    "record_memory_write",
    "record_tool_cache",
    "record_tool_call",
    "record_turn",
    "record_turn_tokens",
]
