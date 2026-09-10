"""工具输入输出 Pydantic schema（工具接口标准）。"""

from __future__ import annotations

from pydantic import BaseModel


class ToolInput(BaseModel):
    """工具入参基类：所有工具的 args_schema 继承此类（extra=forbid 拒绝未知字段）。"""

    model_config = {"extra": "forbid"}
