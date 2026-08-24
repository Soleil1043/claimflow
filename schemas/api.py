"""API 请求/响应 Pydantic schema。

A01 健康检查在此定义；对话类接口（A02-A07）schema 在 T011/T020 补充。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class DependencyStatus(BaseModel):
    """单个依赖的健康状态。"""

    status: Literal["ok", "skipped", "error"]
    detail: str = ""


class HealthResponse(BaseModel):
    """GET /health 响应。"""

    status: Literal["ok", "degraded", "error"]
    profile: str
    dependencies: dict[str, DependencyStatus]
