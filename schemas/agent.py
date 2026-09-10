"""Agent 层相关类型（意图结果、任务计划步骤等，T013/T017/T045 使用）。"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class IntentType(StrEnum):
    """意图五分类（T045 结构化输出枚举）。"""

    simple_faq = "simple_faq"
    single_domain = "single_domain"
    complex_consult = "complex_consult"
    chitchat = "chitchat"
    other = "other"


# 旧值归一映射：历史落库数据中的旧意图名 → 现行枚举值
_LEGACY_INTENT_NAMES = {"multi_step": IntentType.complex_consult.value}


def normalize_intent(value: str | None) -> str | None:
    """意图值归一：multi_step（历史数据）→ complex_consult，其余原样。"""
    if not value:
        return value
    return _LEGACY_INTENT_NAMES.get(value, value)


class IntentClassification(BaseModel):
    """意图分类 LLM 结构化输出（with_structured_output，T045）。"""

    intent: IntentType
    reason: str = ""


class IntentResult(BaseModel):
    """意图分类结果（F03，节点层返回：intent 为枚举值字符串）。"""

    intent: str  # IntentType 枚举值
    reason: str = ""
    # True 表示 LLM 失败走了关键词兜底（可观测性）
    fallback: bool = False


class TaskStep(BaseModel):
    """任务计划单步（T017 Planner 使用）。"""

    step_index: int = 0
    agent: str  # medical / claim
    description: str
    status: str = "pending"  # pending / running / done / failed
    result: dict | None = None


class TaskPlan(BaseModel):
    """多步任务执行计划（T017 Planner 使用）。"""

    steps: list[TaskStep] = Field(default_factory=list)
    # 动态调整标记：执行失败时可触发重规划（T017）
    revised: bool = False
