"""GET /health 健康检查 API 测试。

用 httpx ASGITransport + 内存 SQLite 引擎，不依赖外部服务。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

import services.db.session as session_module
from app.main import app
from services.db.models import Base


@pytest.fixture()
async def client(monkeypatch, tmp_path):
    """内存 SQLite 引擎 + 临时 qdrant 路径 + 测试 LLM Key 的客户端。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(session_module, "_engine", engine)
    monkeypatch.setattr(session_module, "_session_factory", None)

    monkeypatch.setattr(session_module.settings, "qdrant_local_path", str(tmp_path / "qdrant"))
    monkeypatch.setattr(session_module.settings, "llm_api_key", "sk-test-key")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await engine.dispose()


async def test_health_all_ok_in_dev(client: AsyncClient) -> None:
    """dev profile：postgres ok / qdrant local mode / redis skipped / llm ok → 整体 ok。"""
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()

    assert body["status"] == "ok"
    assert body["profile"] == "dev"
    assert body["dependencies"]["postgres"]["status"] == "ok"
    assert body["dependencies"]["qdrant"]["status"] == "ok"
    assert body["dependencies"]["qdrant"]["detail"] == "local mode"
    assert body["dependencies"]["redis"]["status"] == "skipped"
    assert body["dependencies"]["llm"]["status"] == "ok"
    assert body["dependencies"]["llm"]["detail"] == "deepseek-v4-flash"


async def test_health_degraded_when_llm_unconfigured(client, monkeypatch) -> None:
    """LLM Key 未配置 → 整体 degraded，llm error。"""
    import app.api.v1.health as health_module

    monkeypatch.setattr(health_module.settings, "llm_api_key", "  ")
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["llm"]["status"] == "error"


async def test_health_error_when_db_down(client, monkeypatch) -> None:
    """数据库不可用 → 整体 error，postgres error。"""

    async def _boom() -> None:
        raise RuntimeError("db down")

    class _BadEngine:
        def connect(self):
            return _UnavailableConn()

    class _UnavailableConn:
        def __aenter__(self):
            raise RuntimeError("connect refused")

        def __aexit__(self, *args):
            return False

    monkeypatch.setattr("app.api.v1.health.get_engine", lambda: _BadEngine())
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert body["dependencies"]["postgres"]["status"] == "error"
    assert "connect refused" in body["dependencies"]["postgres"]["detail"]


async def test_health_error_when_qdrant_local_path_unwritable(client, monkeypatch) -> None:
    """dev 下 qdrant 路径不可写 → qdrant error（但整体仅 degraded，不阻断启动）。"""
    import app.api.v1.health as health_module

    # 指向一个文件路径，mkdir 必然失败
    monkeypatch.setattr(health_module.settings, "qdrant_local_path", "pyproject.toml/sub")
    resp = await client.get("/health")
    body = resp.json()
    assert body["dependencies"]["qdrant"]["status"] == "error"
