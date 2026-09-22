"""依赖注入：路由层统一从这里获取会话、配置、核赔派发器与坐席鉴权。"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from services.db.session import get_session


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """请求级数据库会话（透传 services.db.session.get_session）。"""
    async for session in get_session():
        yield session


def get_app_settings() -> Settings:
    """全局配置单例。"""
    return settings

def get_case_dispatcher(request: Request) -> Any:
    """应用级案件交付派发器（lifespan 初始化到 app.state.case_dispatcher，T103）。"""
    dispatcher = getattr(request.app.state, "case_dispatcher", None)
    if dispatcher is None:
        msg = "案件派发器未初始化（lifespan 未启动？）"
        raise RuntimeError(msg)
    return dispatcher


async def require_staff(
    x_staff_key: str | None = Header(default=None, alias="X-Staff-Key"),
) -> str | None:
    """坐席端点鉴权（T147，D067 路径 A：多 Key + Key 即身份）。

    - staff_keys 未配置（dev 默认）：放行，返回 None —— 身份回退请求体自报（向后兼容）
    - 已配置：X-Staff-Key 逐 Key 常数时间比对（防时序攻击），通过返回 Key 对应
      坐席身份 —— 身份从此可信，落审计的 operator 不再是自报值
    """
    key_map = settings.staff_key_map
    if not key_map:
        return None
    if x_staff_key is None:
        raise HTTPException(status_code=401, detail="缺少 X-Staff-Key 请求头")
    for key, identity in key_map.items():
        if secrets.compare_digest(x_staff_key, key):
            return identity
    raise HTTPException(status_code=401, detail="无效的坐席凭据")
