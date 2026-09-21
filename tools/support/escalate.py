"""转人工标记工具（T133，D057-3）。

标记式设计：本工具不直接改会话状态——真正的 ai → escalated 流转由
services/support/agent.reply() 在 AI 终局话术落库之后执行（确定性代码）。
这样"转接话术"仍写入 AI 时间线，不撞 T132 状态机的约束（escalated 后
assistant 角色停答）。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from schemas.tools import ToolInput
from tools.base import ClaimflowTool


class EscalateToHumanInput(ToolInput):
    """转人工入参。"""

    reason: str = Field(description="转人工原因（一句话，供坐席工单展示）", min_length=1)


class EscalateToHumanTool(ClaimflowTool):
    name: str = "escalate_to_human"
    description: str = (
        "把当前客服会话转接给人工坐席。以下情况使用：客户明确要求人工客服；"
        "知识检索无法回答客户问题；客户投诉、情绪激动或涉及理赔纠纷。"
        "调用后用一两句话告知客户人工客服即将接入、请保持会话开启。"
    )
    args_schema: type[EscalateToHumanInput] = EscalateToHumanInput

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """同步壳（langchain 1.x 要求实现 _run）：本项目全链路 async。"""
        raise NotImplementedError(f"{self.name} 仅支持异步调用（ainvoke）")

    async def _arun(self, *, reason: str) -> dict[str, Any]:
        return {
            "success": True,
            "hint": "转人工已受理。请收尾并告知用户：人工客服会尽快接入，请勿关闭会话窗口。",
        }
