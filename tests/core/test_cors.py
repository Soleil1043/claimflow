"""跨域白名单测试（T203，C3 纵深）：默认不挂 / 精确 Origin 放行 / 通配符拒收。

C3 主修复在前端（workbench request() 改走同源代理）；后端 CORS 为可选纵深，
默认关闭——这里锁定"默认零行为变化"与"配置后预检按白名单生效"两点。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import app, install_cors


def test_cors_origin_list_parsing() -> None:
    """逗号分隔、去空白、跳空段；"*" 拒收（白名单只认精确 Origin）。"""
    s = Settings(cors_allow_origins=" http://localhost:5173 ,,*, http://a.example ")
    assert s.cors_origin_list == ["http://localhost:5173", "http://a.example"]
    assert Settings(cors_allow_origins="").cors_origin_list == []


def test_default_app_has_no_cors_middleware() -> None:
    """默认配置不挂 CORS：浏览器端均走同源代理，后端不对任意来源开放跨域。"""
    assert all(m.cls is not CORSMiddleware for m in app.user_middleware)


async def test_configured_preflight_allows_listed_origin_only() -> None:
    """配置后：白名单 Origin 预检放行（含坐席鉴权头），非白名单不回 ACAO。"""
    target = FastAPI()

    @target.post("/api/v1/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    install_cors(target, ["http://localhost:5173"])
    async with AsyncClient(transport=ASGITransport(app=target), base_url="http://test") as ac:
        allowed = await ac.options(
            "/api/v1/ping",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-staff-key",
            },
        )
        denied = await ac.options(
            "/api/v1/ping",
            headers={
                "Origin": "http://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "x-staff-key" in allowed.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-origin" not in denied.headers
