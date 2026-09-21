"""LLM token 归集与环节标注（T029 起源；T117 收敛为案件维度）。

机制（contextvars，对节点零侵入）：
- 案件交付外层 `track_case(case_id)` 绑定案件上下文（services.case_jobs）
- 各节点经 observed_ainvoke / phase_ainvoke 调 LLM 时自动归集 usage
  （record_usage_to_tracker → CASE_TOKENS{model} 案件维度 Prometheus 指标）
- 环节（phase）由调用方用 track_phase / phase_ainvoke 标注（ocr / executor / other），
  用于 tracing span 属性

不用图状态字段传递的原因：state 是 LangGraph 管理的合并语义，
节点返回 dict 才生效；观测数据走上下文对节点零侵入。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AnyMessage

from app.core.logging import get_logger
from services.observability import metrics

log = get_logger(__name__)

# 当前环节标注（ocr / executor / other）
_current_phase: ContextVar[str] = ContextVar("claimflow_llm_phase", default="other")
# 当前案件上下文：置位时 LLM 用量按案件维度记 CASE_TOKENS
_current_case: ContextVar[str | None] = ContextVar("claimflow_case_id", default=None)


# ===== 上下文管理 =====


class track_phase:
    """环节标注上下文管理器：with track_phase("ocr"): ...

    同步上下文管理器：进入时切换 _current_phase，退出恢复。
    """

    def __init__(self, phase: str) -> None:
        self._phase = phase
        self._token: Any = None

    def __enter__(self) -> None:
        self._token = _current_phase.set(self._phase)

    def __exit__(self, *exc: Any) -> None:
        _current_phase.reset(self._token)


def current_phase() -> str:
    """当前 LLM 环节标注（tracing span 属性用；无标注时 "other"）。"""
    return _current_phase.get()


def record_usage_to_tracker(model: str, prompt_tokens: int, completion_tokens: int) -> None:
    """observed_ainvoke 回调：案件上下文置位时记 CASE_TOKENS{model} 指标。"""
    case_id = _current_case.get()
    if case_id is not None:
        metrics.record_case_tokens(model, prompt_tokens + completion_tokens)


class UsageRecordingHandler(BaseCallbackHandler):
    """模型级 token 归集回调（T130c 自 worker_agent 迁移为 client 级共享）。

    挂在 get_chat_model 返回的模型实例上——orchestrator 路由 / 材料审查 / 决定书
    撰写 / Worker 子图的全部 LLM 调用统一经 on_llm_end 归集 CASE_TOKENS；
    案件上下文（track_case）未置位时不记录（零案件上下文的裸调用无归宿）。
    之前只挂 Worker 子图：评测 LLM 模式仅路由器走 LLM，token 恒为 0（T125a 根因）。
    """

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        try:
            message = response.generations[0][0].message
            usage = getattr(message, "usage_metadata", None) or {}
            model = (response.llm_output or {}).get("model_name") or "unknown"
            if usage:
                record_usage_to_tracker(
                    model,
                    int(usage.get("input_tokens", 0)),
                    int(usage.get("output_tokens", 0)),
                )
        except (AttributeError, IndexError, TypeError, ValueError):
            pass  # 非标准响应（测试假件等）：记账跳过，不影响执行


@contextmanager
def track_case(case_id: str):
    """案件维度 token 归集上下文（案件交付在图调用外包裹）。"""
    token = _current_case.set(case_id)
    try:
        yield
    finally:
        _current_case.reset(token)


async def phase_ainvoke(
    model: BaseChatModel,
    messages: list[AnyMessage],
    *,
    phase: str,
    config: dict[str, Any] | None = None,
) -> Any:
    """带环节标注的 observed_ainvoke：指标埋点 + 案件维度归集一步完成。"""
    from services.observability.llm_metrics import observed_ainvoke

    with track_phase(phase):
        return await observed_ainvoke(model, messages, config=config)
