"""记忆治理 API 测试（T138）：DELETE 档案条目（审计留痕）+ 404。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
import services.memory.long_term as lt
from app.main import app
from services.db.models import Base, CaseEvent
from services.db.session import dispose_engine
from services.memory.case_memory import write_case_memory


@pytest.fixture()
async def client(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """文件库（审计事件落库）+ 开记忆 + 桩嵌入 + 测试客户端。"""

    def fake_embed(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "拒赔" in t or "rejected" in t else [0.0, 1.0] for t in texts]

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'memory_api_test.db').as_posix()}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module.swap_engine(engine, factory)

    monkeypatch.setattr(lt, "_embed_for_store", fake_embed)
    monkeypatch.setattr(lt.settings, "memory_enabled", True)
    lt.reset_memory_store()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, factory
    lt.reset_memory_store()
    await engine.dispose()
    await dispose_engine()


async def test_delete_memory_endpoint_with_audit(client) -> None:
    """删除：200 + Store 条目消失 + CaseEvent human 审计留痕。"""
    ac, factory = client
    await write_case_memory(
        {
            "case_id": "CASE-2026-0001",
            "user_id": "u-1",
            "case_type": "medical",
            "final_decision": "approved",
        },
        outcome="auto_issued",
    )

    resp = await ac.request(
        "DELETE",
        "/api/v1/memory/u-1/entries/CASE-2026-0001",
        json={"agent": "agent-01"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "user_id": "u-1", "case_id": "CASE-2026-0001"}

    async with factory() as s:
        events = (
            (await s.execute(select(CaseEvent).where(CaseEvent.case_id == "CASE-2026-0001")))
            .scalars()
            .all()
        )
    assert len(events) == 1
    assert events[0].kind == "human"
    assert events[0].payload["action"] == "memory_deleted"
    assert events[0].payload["operator"] == "agent-01"

    # 删除后 404（条目已不存在）
    again = await ac.request("DELETE", "/api/v1/memory/u-1/entries/CASE-2026-0001")
    assert again.status_code == 404


async def test_delete_memory_not_found(client) -> None:
    ac, _ = client
    resp = await ac.request("DELETE", "/api/v1/memory/u-none/entries/CASE-NOT-EXIST")
    assert resp.status_code == 404
