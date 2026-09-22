"""坐席端点鉴权测试（T147，D067 路径 A：多 Key + Key 即身份）。

矩阵：dev 未配置放行 / 配置后 401（无头、错 Key）/ 对 Key 200 / 客户端点不外溢；
身份派生：require_staff 返回 Key 对应坐席名（可信身份）；
prod 启动校验：prod + 空 STAFF_KEYS 拒绝构造。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
from app.api import dependencies as deps
from app.core.config import Settings
from app.main import app
from services.db.models import Base

STAFF_KEYS = "alice:key-alice-123,bob:key-bob-456"


async def _mk_client(tmp_path):
    """临时库 ASGI 客户端（staff_keys 由调用方 monkeypatch 设置，防裸赋值泄漏）。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'auth_test.db').as_posix()}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module.swap_engine(engine, factory)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), engine


async def _run(tmp_path, monkeypatch: pytest.MonkeyPatch, staff_keys: str, fn) -> None:
    """样板：monkeypatch 配 staff_keys → 建客户端 → 执行断言 → 清理（engine 释放）。"""
    monkeypatch.setattr(deps.settings, "staff_keys", staff_keys)
    client, engine = await _mk_client(tmp_path)
    try:
        await fn(client)
    finally:
        await client.aclose()
        await engine.dispose()


class TestStaffAuthMatrix:
    async def test_dev_open_without_header(self, tmp_path, monkeypatch):
        """staff_keys 未配置（dev 默认）：坐席端点无 header 放行（向后兼容）。"""

        async def check(client: AsyncClient):
            assert (await client.get("/api/v1/interventions/cases")).status_code == 200

        await _run(tmp_path, monkeypatch, "", check)

    async def test_missing_header_401(self, tmp_path, monkeypatch):
        async def check(client: AsyncClient):
            assert (await client.get("/api/v1/interventions/cases")).status_code == 401

        await _run(tmp_path, monkeypatch, STAFF_KEYS, check)

    async def test_wrong_key_401(self, tmp_path, monkeypatch):
        async def check(client: AsyncClient):
            resp = await client.get(
                "/api/v1/interventions/cases", headers={"X-Staff-Key": "key-wrong"}
            )
            assert resp.status_code == 401

        await _run(tmp_path, monkeypatch, STAFF_KEYS, check)

    async def test_valid_key_all_staff_read_endpoints(self, tmp_path, monkeypatch):
        """对 Key 放行——覆盖全部坐席只读端点（工单列表 / 抽评队列 / 客服工单队列）。"""

        async def check(client: AsyncClient):
            headers = {"X-Staff-Key": "key-alice-123"}
            assert (
                await client.get("/api/v1/interventions/cases", headers=headers)
            ).status_code == 200
            assert (
                await client.get(
                    "/api/v1/interventions/narrative-samples", headers=headers
                )
            ).status_code == 200
            assert (
                await client.get("/api/v1/support/tickets", headers=headers)
            ).status_code == 200

        await _run(tmp_path, monkeypatch, STAFF_KEYS, check)

    async def test_customer_endpoint_not_affected(self, tmp_path, monkeypatch):
        """客户端点不受鉴权影响：无 header 建客服会话 201。"""

        async def check(client: AsyncClient):
            assert (
                await client.post("/api/v1/support/conversations")
            ).status_code == 201

        await _run(tmp_path, monkeypatch, STAFF_KEYS, check)


class TestIdentityDerivation:
    async def test_require_staff_returns_identity(self, monkeypatch):
        monkeypatch.setattr(deps.settings, "staff_keys", STAFF_KEYS)
        assert await deps.require_staff("key-alice-123") == "alice"
        assert await deps.require_staff("key-bob-456") == "bob"

    async def test_require_staff_open_returns_none(self, monkeypatch):
        monkeypatch.setattr(deps.settings, "staff_keys", "")
        assert await deps.require_staff(None) is None

    async def test_require_staff_wrong_key_401(self, monkeypatch):
        from fastapi import HTTPException

        monkeypatch.setattr(deps.settings, "staff_keys", STAFF_KEYS)
        with pytest.raises(HTTPException) as exc:
            await deps.require_staff("nope")
        assert exc.value.status_code == 401

    def test_key_map_parsing(self, monkeypatch):
        monkeypatch.setattr(deps.settings, "staff_keys", STAFF_KEYS)
        assert deps.settings.staff_key_map == {
            "key-alice-123": "alice",
            "key-bob-456": "bob",
        }

    def test_key_map_tolerates_garbage(self, monkeypatch):
        """解析容错：空段 / 缺冒号 / 空名或空 Key 跳过不抛错。"""
        monkeypatch.setattr(
            deps.settings,
            "staff_keys",
            " , bad-pair-no-colon, :no-name, name-with-empty-key:,alice:k1",
        )
        assert deps.settings.staff_key_map == {"k1": "alice"}


class TestProdValidation:
    def test_prod_without_staff_keys_rejected(self):
        with pytest.raises(ValidationError, match="STAFF_KEYS"):
            Settings(_env_file=None, app_profile="prod", staff_keys="")

    def test_prod_with_staff_keys_ok(self):
        s = Settings(_env_file=None, app_profile="prod", staff_keys="alice:k1")
        assert s.staff_key_map == {"k1": "alice"}

    def test_dev_without_staff_keys_ok(self):
        s = Settings(_env_file=None, app_profile="dev", staff_keys="")
        assert s.staff_key_map == {}
