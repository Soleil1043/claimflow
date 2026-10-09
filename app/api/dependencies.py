"""依赖注入：路由层统一从这里获取会话、配置、核赔派发器与坐席鉴权。"""

from __future__ import annotations

import hashlib
import math
import secrets
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from fastapi import Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from services.db.session import get_session

if TYPE_CHECKING:
    from services.db.models import Case


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


def _staff_key_valid(x_staff_key: str | None) -> bool:
    """坐席 Key 是否有效（不抛异常的探测版，供案件双通道校验复用）。"""
    if x_staff_key is None:
        return False
    return any(
        secrets.compare_digest(x_staff_key, key) for key in settings.staff_key_map
    )


def verify_case_access(
    case: Case,
    x_case_token: str | None,
    x_staff_key: str | None,
) -> None:
    """案件门户访问校验（T171，D077）：申请人令牌与坐席 Key 双通道。

    放行顺序：
    1. staff_keys 未配置（dev 开放态）——沿 T147 兼容口径放行（workbench 零改动）
    2. 有效 X-Staff-Key——坐席通道
    3. X-Case-token 与案件哈希常数时间比对——门户申请人通道（创建回执持有者）
    其余（缺失 / 不匹配 / 旧库 NULL 哈希行无令牌可对）一律 401。
    """
    if not settings.staff_key_map:
        return
    if _staff_key_valid(x_staff_key):
        return
    if x_case_token is not None and case.access_token_hash is not None:
        digest = hashlib.sha256(x_case_token.encode("utf-8")).hexdigest()
        if secrets.compare_digest(digest, case.access_token_hash):
            return
    raise HTTPException(
        status_code=401, detail="无权访问该案件（需 X-Case-Token 或 X-Staff-Key）"
    )


def rate_limit(scope: str, per_minute_setting: str):
    """门户限流依赖工厂（T172，D078）：按客户端 IP 的令牌桶，超限 429。

    - per_minute_setting 是 Settings 字段名（调用时读取，测试可 monkeypatch）
    - 配额 0 = 该域不限流（负载测试口径）
    - 429 携带 Retry-After（下次可用令牌的秒数，向上取整）
    """

    async def _enforce(request: Request) -> None:
        from services.ratelimit import client_ip, get_rate_limiter, note_rejection

        per_minute: int = getattr(settings, per_minute_setting)
        limiter = await get_rate_limiter()
        wait = await limiter.acquire(scope, client_ip(request), per_minute)
        if wait is not None:
            note_rejection(scope)
            retry_after = max(1, math.ceil(wait))
            raise HTTPException(
                status_code=429,
                detail="请求过于频繁，请稍后再试",
                headers={"Retry-After": str(retry_after)},
            )

    return _enforce
