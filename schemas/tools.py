"""工具输入输出 Pydantic schema（工具接口标准）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolInput(BaseModel):
    """工具入参基类：所有工具的 args_schema 继承此类（extra=forbid 拒绝未知字段）。"""

    model_config = {"extra": "forbid"}


class ToolOutput(BaseModel):
    """工具输出基类：统一 success / error_message / data 三段结构。

    约定：业务失败（如保单不存在）不算异常——返回 success=False +
    error_message，由上层 Agent 决定如何向用户解释；
    系统级失败（超时、网络）由守卫层（tools/guards.py）抛 ToolExecutionError。
    """

    success: bool
    error_message: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
