"""叙述抽评 API 测试（T139）：队列列表（pending + 统计）/ 评审落审计 / 409 / 404。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
from app.main import app
from services.db.models import Base, Case, CaseEvent, DecisionDocument
from services.db.session import dispose_engine


@pytest.fixture()
async def client(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """文件库 + 种子（1 案采样未评 / 1 案采样已评 / 1 案未采样）+ 测试客户端。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'samples_test.db').as_posix()}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module.swap_engine(engine, factory)

    async with factory() as s:
        for cid, ctype, decision, amount in (
            ("CASE-S-0001", "medical", "approved", Decimal("4640.00")),
            ("CASE-S-0002", "auto", "rejected", Decimal("0.00")),
            ("CASE-S-0003", "property", "approved", Decimal("8000.00")),
        ):
            s.add(
                Case(
                    id=cid, user_id=f"u-{cid[-4:]}", policy_no="POL-2025-0001",
                    case_type=ctype, status="auto_issued",
                    claimed_amount=amount, approved_amount=amount,
                    incident_date=dt.date(2026, 8, 10),
                    incident_description="测试案", final_decision=decision,
                )
            )
            s.add(
                DecisionDocument(
                    case_id=cid, version=1, title="理赔决定书",
                    body=f"{cid} 核定依据叙述段……", conclusion=decision,
                    approved_amount=amount, issued_by="auto",
                )
            )
        # 0001/0002 采样；0002 已评审（pass）
        s.add(CaseEvent(case_id="CASE-S-0001", kind="narrative_sample", seq=1, payload={}))
        s.add(CaseEvent(case_id="CASE-S-0002", kind="narrative_sample", seq=1, payload={}))
        s.add(
            CaseEvent(
                case_id="CASE-S-0002", kind="narrative_review", seq=2,
                payload={"verdict": "pass", "comment": "叙述准确", "operator": "agent-01"},
            )
        )
        await s.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, factory
    await engine.dispose()
    await dispose_engine()


async def test_list_pending_and_stats(client) -> None:
    """队列：pending=0001（0002 已评排除、0003 未采样不入）；统计含通过率口径。"""
    ac, _ = client
    resp = await ac.get("/api/v1/interventions/narrative-samples")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["case_id"] == "CASE-S-0001"
    assert item["final_decision"] == "approved"
    assert "核定依据叙述段" in item["narrative"]
    assert body["stats"] == {"sampled": 2, "reviewed": 1, "passed": 1, "revised": 0}


async def test_review_flow_and_audit(client) -> None:
    """评审：落 narrative_review 事件（verdict/comment/operator）→ 队列出清。"""
    ac, factory = client
    resp = await ac.post(
        "/api/v1/interventions/cases/CASE-S-0001/narrative-review",
        json={"agent": "agent-02", "verdict": "revise", "comment": "引用条款编号有误"},
    )
    assert resp.status_code == 200
    assert resp.json()["verdict"] == "revise"

    async with factory() as s:
        events = list(
            (
                await s.execute(
                    select(CaseEvent).where(CaseEvent.case_id == "CASE-S-0001")
                )
            )
            .scalars()
            .all()
        )
    assert events, "评审事件已落库"
    review = [e for e in events if e.kind == "narrative_review"]
    assert len(review) == 1
    assert review[0].payload["verdict"] == "revise"
    assert review[0].payload["operator"] == "agent-02"

    after = (await ac.get("/api/v1/interventions/narrative-samples")).json()
    assert after["total"] == 0
    assert after["stats"]["reviewed"] == 2
    assert after["stats"]["revised"] == 1


async def test_review_conflict_and_not_sampled(client) -> None:
    """已评审 409；未采样案 404；不存在案 404。"""
    ac, _ = client
    conflict = await ac.post(
        "/api/v1/interventions/cases/CASE-S-0002/narrative-review",
        json={"agent": "a", "verdict": "pass"},
    )
    assert conflict.status_code == 409

    not_sampled = await ac.post(
        "/api/v1/interventions/cases/CASE-S-0003/narrative-review",
        json={"agent": "a", "verdict": "pass"},
    )
    assert not_sampled.status_code == 404

    missing = await ac.post(
        "/api/v1/interventions/cases/CASE-NONE/narrative-review",
        json={"agent": "a", "verdict": "pass"},
    )
    assert missing.status_code == 404
