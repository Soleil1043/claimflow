"""工具工厂（T044）：显式装配带守卫的工具实例。

装配链：raw 工具（ClaimflowTool：args_schema + _arun）
  → Runnable `.with_retry()`（官方重试，指数退避）
  → GuardedTool（缓存白名单 → 熔断 → 超时，tools/guards.py）

默认工具图 `get_default_tool_map()` 为进程级惰性单例——熔断器随工具对象常驻，
跨会话共享（这正是守卫放工具层而非图层的理由）。
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from tools.base import ClaimflowTool
from tools.guards import CircuitBreaker, GuardedTool

# 守卫默认参数：10s 超时 / 2 次重试 / 5 次连续失败熔断 30s
DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_INITIAL = 0.5
DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_BREAKER_COOLDOWN = 30.0

_tool_map: dict[str, BaseTool] | None = None


def build_raw_tools() -> list[ClaimflowTool]:
    """构造全部原始工具（DI 取全局缺省；测试可单独构造替换）。"""
    from tools.claim.calculator import ClaimCalculatorTool
    from tools.claim.claim_rule_rag import ClaimRuleRagTool
    from tools.claim.policy_query import PolicyQueryTool
    from tools.compliance.risk_scoring import RiskScoringTool
    from tools.compliance.rule_check import ComplianceRuleCheckTool
    from tools.compliance.sensitive_filter import SensitiveFilterTool
    from tools.fraud.blacklist import QueryBlacklistTool
    from tools.fraud.history import ClaimsHistoryTool
    from tools.fraud.rules import FraudRulesTool
    from tools.medical.diagnosis_matcher import DiagnosisMatcherTool
    from tools.medical.ocr_extract import OcrExtractTool
    from tools.medical.record_query import RecordQueryTool

    return [
        PolicyQueryTool(),
        ClaimCalculatorTool(),
        ClaimRuleRagTool(),
        RecordQueryTool(),
        DiagnosisMatcherTool(),
        OcrExtractTool(),
        ComplianceRuleCheckTool(),
        SensitiveFilterTool(),
        RiskScoringTool(),
        QueryBlacklistTool(),
        ClaimsHistoryTool(),
        FraudRulesTool(),
    ]


def assemble_tool(
    raw: ClaimflowTool,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_initial: float = DEFAULT_BACKOFF_INITIAL,
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
    breaker_cooldown: float = DEFAULT_BREAKER_COOLDOWN,
    fallback: dict[str, Any] | None = None,
    enable_cache: bool = True,
) -> GuardedTool:
    """单个工具装配：官方重试（内层）+ 守卫（缓存/熔断/超时，外层）。

    重试范围 = 全部异常（瞬时/系统故障均退避重试）；
    stop_after_attempt = max_retries + 1，即初始 1 次 + 重试 N 次。
    """
    retried: Runnable = raw.with_retry(
        stop_after_attempt=max_retries + 1,
        wait_exponential_jitter=True,
        exponential_jitter_params={"initial": backoff_initial, "max": backoff_initial * 4},
    )
    return GuardedTool(
        name=raw.name,
        description=raw.description,
        args_schema=raw.args_schema,
        inner=retried,
        breaker=CircuitBreaker(failure_threshold, breaker_cooldown),
        timeout_s=timeout_s,
        fallback=fallback,
        cache_enabled=enable_cache,
    )


def get_default_tool_map() -> dict[str, BaseTool]:
    """默认工具图（惰性单例；守卫/熔断器随实例常驻进程）。"""
    global _tool_map
    if _tool_map is None:
        _tool_map = {tool.name: assemble_tool(tool) for tool in build_raw_tools()}
    return _tool_map


def reset_tool_map() -> None:
    """重置默认工具图（测试用）。"""
    global _tool_map
    _tool_map = None
