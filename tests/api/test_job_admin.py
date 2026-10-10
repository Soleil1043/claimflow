"""死信队列 API 测试（T182，D087）：清单 / 重驱（含审计事件）/ 409 / 404。

矩阵：
- GET /interventions/jobs/dead 只列 dead（含 attempts/last_error 供核对死因）
- POST /interventions/jobs/{id}/redrive：dead → queued + job_redrive 审计事件
  （operator 由 staff Key 派生，dev 未配置回退 unknown）
- 状态门 409（非 dead）、任务不存在 404
- 幂等边界：重驱后再次重驱（已 queued）→ 409
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
from app.main import app
from services.db.models import Base, Case, CaseEvent, CaseJob
from services.db.session import dispose_engine


@pytest.fixture()
async def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """文件库 + 种子（1 dead / 1 queued / 1 succeeded 任务）+ 测试客户端。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'jobadmin_test.db').as_posix()}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module.swap_engine(engine, factory)

    async with factory() as s:
        for idx, (cid, status_) in enumerate(
            (("CASE-D-0001", "dead"), ("CASE-D-0002", "queued"), ("CASE-D-0003", "succeeded"))
        ):
            s.add(
                Case(
                    id=cid, user_id=f"u-{cid[-4:]}", policy_no=f"POL-2025-{idx:04d}",
                    case_type="medical", status="in_progress",
                    claimed_amount=Decimal("1000.00"),
                    incident_date=dt.date(2026, 8, 1),
                    incident_description="死信测试案",
                )
            )
            s.add(
                CaseJob(
                    case_id=cid, action="run", payload={"case_id": cid},
                    status=status_, attempt=3 if status_ == "dead" else 0,
                    max_attempts=3, run_after=dt.datetime.now(),
                    last_error="图执行三次耗尽" if status_ == "dead" else None,
                    finished_at=dt.datetime.now() if status_ != "queued" else None,
                )
            )
        await s.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, factory
    await dispose_engine()


async def test_list_dead_jobs_filters_and_carries_error(client) -> None:
    c, _ = client
    resp = await c.get("/api/v1/interventions/jobs/dead")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["case_id"] == "CASE-D-0001"
    assert item["attempts"] == 3
    assert item["last_error"] == "图执行三次耗尽"


async def test_redrive_resets_and_audits(client) -> None:
    c, factory = client
    async with factory() as s:
        dead_id = (
            await s.execute(select(CaseJob).where(CaseJob.status == "dead"))
        ).scalar_one().id

    resp = await c.post(f"/api/v1/interventions/jobs/{dead_id}/redrive")
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == dead_id
    assert body["status"] == "queued"
    assert body["operator"] == "unknown", "dev 未配置 staff_keys 回退 unknown"

    async with factory() as s:
        job = await s.get(CaseJob, dead_id)
        assert job.status == "queued" and job.attempt == 0
        events = (
            (
                await s.execute(
                    select(CaseEvent).where(CaseEvent.case_id == job.case_id)
                )
            )
            .scalars()
            .all()
        )
    redrive_events = [e for e in events if e.kind == "job_redrive"]
    assert len(redrive_events) == 1
    assert redrive_events[0].payload["operator"] == "unknown"
    assert redrive_events[0].payload["job_id"] == dead_id


async def test_redrive_conflicts_on_non_dead(client) -> None:
    c, factory = client
    async with factory() as s:
        live_id = (
            await s.execute(select(CaseJob).where(CaseJob.status == "queued"))
        ).scalar_one().id

    resp = await c.post(f"/api/v1/interventions/jobs/{live_id}/redrive")
    assert resp.status_code == 409
    assert "仅 dead" in resp.json()["detail"]


async def test_redrive_twice_second_conflicts(client) -> None:
    """重驱幂等边界：第一次 200 后任务已 queued，再次重驱 409（不重复给额度）。"""
    c, factory = client
    async with factory() as s:
        dead_id = (
            await s.execute(select(CaseJob).where(CaseJob.status == "dead"))
        ).scalar_one().id

    first = await c.post(f"/api/v1/interventions/jobs/{dead_id}/redrive")
    second = await c.post(f"/api/v1/interventions/jobs/{dead_id}/redrive")
    assert first.status_code == 200
    assert second.status_code == 409


async def test_redrive_unknown_job_404(client) -> None:
    c, _ = client
    resp = await c.post("/api/v1/interventions/jobs/999999/redrive")
    assert resp.status_code == 404
