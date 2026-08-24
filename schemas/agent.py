"""Agent 层相关类型（意图结果、任务计划步骤等，T013/T017 使用）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class IntentResult(BaseModel):
    """意图分类结果（F03）。"""

    intent: str  # simple_faq / single_domain / multi_step / chitchat / other
    reason: str = ""
    # True 表示 LLM 失败走了关键词兜底（可观测性）
    fallback: bool = False


class TaskStep(BaseModel):
    """任务计划单步（T017 Planner 使用）。"""

    step_index: int
    agent: str  # medical / claim
    description: str
    status: str = "pending"  # pending / running / done / failed
    result: dict | None = None


class TaskPlan(BaseModel):
    """多步任务执行计划（T017 Planner 使用）。"""

    steps: list[TaskStep] = Field(default_factory=list)
    # 动态调整标记：执行失败时可触发重规划（T017）
    revised: bool = False
