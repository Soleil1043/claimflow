"""轮次 Token 统计与预算控制（T029，architecture.md 8.3）。

机制：contextvars 跨节点传递 TurnTokenTracker——
- A06 入口 `start_turn_tokens()` 创建 tracker 并写入上下文
- 各节点经 observed_ainvoke 调 LLM 时自动归集 usage（无需节点感知）
- 环节（phase）由调用方用 track_phase / phase_ainvoke 标注
- A06 出口 `finish_turn_tokens()` 汇总：结构化日志 + Prometheus 指标 + 超预算告警

不用 AgentState 字段传递的原因：state 是 LangGraph 管理的合并语义，
节点返回 dict 才生效；观测数据走上下文对节点零侵入。
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AnyMessage

from app.core.config import settings
from app.core.logging import get_logger
from services.observability import metrics

log = get_logger(__name__)

# 当前轮次的 tracker（无请求上下文时为 None，如脚本/测试直调）
_current_tracker: ContextVar[TurnTokenTracker | None] = ContextVar(
    "claimflow_turn_tokens", default=None
)
# 当前环节标注（intent / planner / executor / generator / compliance）
_current_phase: ContextVar[str] = ContextVar("claimflow_llm_phase", default="other")


@dataclass
class TurnTokenTracker:
    """单轮对话的 token 用量归集器（按环节分桶）。"""

    conversation_id: str = ""
    # 环节 → {model → [prompt, completion]}
    usage: dict[str, dict[str, list[int]]] = field(default_factory=dict)

    def add(self, model: str, prompt_tokens: int, completion_tokens: int, phase: str) -> None:
        """归集一次 LLM 调用的用量。"""
        bucket = self.usage.setdefault(phase, {}).setdefault(model, [0, 0])
        bucket[0] += prompt_tokens
        bucket[1] += completion_tokens

    @property
    def total_tokens(self) -> int:
        """本轮总 token（prompt + completion，全部环节）。"""
        return sum(p + c for models in self.usage.values() for p, c in models.values())

    @property
    def prompt_tokens(self) -> int:
        return sum(p for models in self.usage.values() for p, _ in models.values())

    @property
    def completion_tokens(self) -> int:
        return sum(c for models in self.usage.values() for _, c in models.values())

    def phase_tokens(self, phase: str) -> int:
        """单环节 token（该环节所有模型 prompt+completion）。"""
        return sum(p + c for p, c in self.usage.get(phase, {}).values())

    def to_dict(self) -> dict[str, Any]:
        """日志/审计用的扁平结构。"""
        return {
            "total": self.total_tokens,
            "prompt": self.prompt_tokens,
            "completion": self.completion_tokens,
            "phases": {
                phase: {model: {"prompt": p, "completion": c} for model, (p, c) in models.items()}
                for phase, models in self.usage.items()
            },
        }


# ===== 上下文管理 =====


def start_turn_tokens(conversation_id: str) -> TurnTokenTracker:
    """A06 入口调用：创建本轮 tracker 并绑定上下文。"""
    tracker = TurnTokenTracker(conversation_id=conversation_id)
    _current_tracker.set(tracker)
    return tracker


def finish_turn_tokens(tracker: TurnTokenTracker) -> dict[str, Any]:
    """A06 出口调用：汇总输出结构化日志 + Prometheus 指标 + 超预算告警。

    返回扁平 dict（供调用方写入结构化日志 / 审计）。
    超预算只告警不阻断（验收约定）。
    """
    usage = tracker.to_dict()

    # 分环节 Prometheus 指标
    for phase, models in tracker.usage.items():
        for model, (p, c) in models.items():
            metrics.record_turn_tokens(phase=phase, model=model, tokens=p + c)

    # 超预算告警（不阻断）
    budget = settings.turn_token_budget
    if budget > 0 and tracker.total_tokens > budget:
        log.warning(
            "turn_token_budget_exceeded",
            conversation_id=tracker.conversation_id,
            total_tokens=tracker.total_tokens,
            budget=budget,
            phases={ph: tracker.phase_tokens(ph) for ph in tracker.usage},
        )
    else:
        log.info(
            "turn_tokens_summary",
            conversation_id=tracker.conversation_id,
            total=usage["total"],
            prompt=usage["prompt"],
            completion=usage["completion"],
        )

    # 上下文清理（防泄漏到下一请求）
    _current_tracker.set(None)
    return usage


class track_phase:
    """环节标注上下文管理器：with track_phase("intent"): ...

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
    """observed_ainvoke 回调：归集到当前上下文的 tracker（若有）。"""
    tracker = _current_tracker.get()
    if tracker is not None:
        tracker.add(model, prompt_tokens, completion_tokens, _current_phase.get())


async def phase_ainvoke(
    model: BaseChatModel,
    messages: list[AnyMessage],
    *,
    phase: str,
    config: dict[str, Any] | None = None,
) -> Any:
    """带环节标注的 observed_ainvoke：指标埋点 + tracker 归集一步完成。

    各节点替换 observed_ainvoke 时传 phase（intent/planner/executor/generator/compliance）。
    """
    from services.observability.llm_metrics import observed_ainvoke

    with track_phase(phase):
        return await observed_ainvoke(model, messages, config=config)
