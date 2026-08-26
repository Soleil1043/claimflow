"""HITL 人工介入工单测试（T036）。

覆盖：ensure_human_ticket 幂等落单、状态机流转（pending → resolved / transferred_out，
终态 409）、聚合上下文（会话轨迹 tool_trace / agent_steps / 合规快照 / 拦截原因）、
列表 status 筛选与分页。A06 联动（REJECT 自动落单）见 test_a06_scenarios.py 场景 4。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
from app.api.v1.interventions import ensure_human_ticket
from app.main import app
from services.db.models import Base, Conversation, Message

_COMPLIANCE_SNAPSHOT: dict[str, Any] = {
    "verdict": "REJECT",
    "violations": [{"type": "FRAUD_RISK", "detail": "骗保话术"}],
    "risk_score": 98,
    "reason": "欺诈风险",
}


@pytest.fixture()
async def client(monkeypatch):
    """内存 SQLite + 工单路由测试客户端。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "_engine", engine)
    monkeypatch.setattr(session_module, "_session_factory", factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, factory
    await engine.dispose()


async def _seed_conversation(factory, *, user_id: str = "u-hitl") -> uuid.UUID:
    """造一个会话 + 两轮审计消息（带 tool_trace / agent_steps / compliance_status）。"""
    async with factory() as session:
        conv = Conversation(user_id=user_id)
        session.add(conv)
        await session.flush()
        cid = conv.id
        session.add(Message(conversation_id=cid, role="user", content="怎么才能多赔点"))
        session.add(
            Message(
                conversation_id=cid,
                role="assistant",
                content="您的问题需要人工处理，已为您转接。",
                intent="single_domain",
                tool_trace=[{"tool": "policy_query", "input": {}, "output": {"success": True}}],
                agent_steps=[{"agent": "claim", "status": "done", "summary": "核算结论"}],
                compliance_status="REJECT",
            )
        )
        await session.commit()
        return cid


async def _make_ticket(factory, cid: uuid.UUID) -> int:
    async with factory() as session:
        ticket = await ensure_human_ticket(
            session,
            conversation_id=cid,
            user_id="u-hitl",
            intervention_reason="欺诈风险话术，需人工复核",
            compliance_snapshot=_COMPLIANCE_SNAPSHOT,
        )
        await session.commit()
        assert ticket is not None
        return ticket.id


# ---------- 落单幂等 ----------


async def test_ensure_ticket_idempotent_while_open(client) -> None:
    """同会话存在 pending 工单时重复落单被跳过；resolve 后再转人工产生新工单。"""
    _, factory = client
    cid = await _seed_conversation(factory)
    first = await _make_ticket(factory, cid)

    async with factory() as session:
        again = await ensure_human_ticket(
            session,
            conversation_id=cid,
            user_id="u-hitl",
            intervention_reason="再次转人工",
            compliance_snapshot=None,
        )
        await session.commit()
    assert again is None  # open 工单存在，幂等跳过

    # 终态后可再落新单
    async with factory() as session:
        from sqlalchemy import select

        from services.db.models import HumanTicket

        t = (await session.execute(select(HumanTicket).where(HumanTicket.id == first))).scalar_one()
        t.status = "resolved"
        await session.commit()
    async with factory() as session:
        new_ticket = await ensure_human_ticket(
            session,
            conversation_id=cid,
            user_id="u-hitl",
            intervention_reason="再次转人工",
            compliance_snapshot=None,
        )
        await session.commit()
    assert new_ticket is not None and new_ticket.id != first


# ---------- 列表 ----------


async def test_list_empty(client) -> None:
    ac, _ = client
    resp = await ac.get("/api/v1/interventions")
    assert resp.status_code == 200
    assert resp.json() == {"total": 0, "items": []}


async def test_list_status_filter_and_order(client) -> None:
    """列表：倒序 + status 筛选（resolved 的不出现在 pending 队列）。"""
    ac, factory = client
    cid1 = await _seed_conversation(factory, user_id="u1")
    cid2 = await _seed_conversation(factory, user_id="u2")
    t1 = await _make_ticket(factory, cid1)
    t2 = await _make_ticket(factory, cid2)

    resp = await ac.get("/api/v1/interventions")
    body = resp.json()
    assert body["total"] == 2
    assert [i["id"] for i in body["items"]] == [t2, t1]  # 倒序

    # t1 解决后 pending 队列只剩 t2
    await ac.post(
        f"/api/v1/interventions/{t1}/resolve",
        json={"resolution_note": "已电话核实", "resolved_by": "agent-01"},
    )
    resp = await ac.get("/api/v1/interventions", params={"status": "pending"})
    body = resp.json()
    assert body["total"] == 1 and body["items"][0]["id"] == t2
    resp = await ac.get("/api/v1/interventions", params={"status": "resolved"})
    assert resp.json()["total"] == 1


# ---------- 详情聚合上下文 ----------


async def test_detail_aggregates_context(client) -> None:
    """详情：会话轨迹（含 tool_trace/agent_steps/compliance_status）+ 合规快照 + 拦截原因。"""
    ac, factory = client
    cid = await _seed_conversation(factory)
    ticket_id = await _make_ticket(factory, cid)

    resp = await ac.get(f"/api/v1/interventions/{ticket_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["intervention_reason"] == "欺诈风险话术，需人工复核"
    assert body["compliance_snapshot"]["verdict"] == "REJECT"
    assert body["compliance_snapshot"]["risk_score"] == 98
    assert body["conversation"]["id"] == str(cid)
    assert body["conversation"]["status"] == "active"
    assert len(body["messages"]) == 2  # 会话完整轨迹
    assistant = body["messages"][1]
    assert assistant["compliance_status"] == "REJECT"
    assert assistant["tool_trace"][0]["tool"] == "policy_query"
    assert assistant["agent_steps"][0]["agent"] == "claim"


async def test_detail_404(client) -> None:
    ac, _ = client
    assert (await ac.get("/api/v1/interventions/999")).status_code == 404


# ---------- 状态机流转 ----------


async def test_resolve_pending_ticket(client) -> None:
    """resolve：pending → resolved，结论与坐席标识回写。"""
    ac, factory = client
    cid = await _seed_conversation(factory)
    ticket_id = await _make_ticket(factory, cid)

    resp = await ac.post(
        f"/api/v1/interventions/{ticket_id}/resolve",
        json={"resolution_note": "已电话核实并处理", "resolved_by": "agent-01"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "resolved"
    detail = (await ac.get(f"/api/v1/interventions/{ticket_id}")).json()
    assert detail["resolution_note"] == "已电话核实并处理"
    assert detail["resolved_by"] == "agent-01"


async def test_escalate_pending_ticket(client) -> None:
    """escalate：pending → transferred_out（升级转出）。"""
    ac, factory = client
    cid = await _seed_conversation(factory)
    ticket_id = await _make_ticket(factory, cid)

    resp = await ac.post(
        f"/api/v1/interventions/{ticket_id}/escalate",
        json={"note": "涉案金额较高，转反欺诈组", "resolved_by": "agent-02"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "transferred_out"


async def test_terminal_state_rejects_further_actions(client) -> None:
    """终态守卫：resolved / transferred_out 再流转一律 409。"""
    ac, factory = client
    cid = await _seed_conversation(factory)
    ticket_id = await _make_ticket(factory, cid)
    await ac.post(
        f"/api/v1/interventions/{ticket_id}/resolve",
        json={"resolution_note": "已处理", "resolved_by": "agent-01"},
    )
    # resolved → resolve / escalate 均 409
    resp = await ac.post(
        f"/api/v1/interventions/{ticket_id}/resolve",
        json={"resolution_note": "再处理", "resolved_by": "agent-01"},
    )
    assert resp.status_code == 409
    resp = await ac.post(
        f"/api/v1/interventions/{ticket_id}/escalate",
        json={"resolved_by": "agent-01"},
    )
    assert resp.status_code == 409

    # transferred_out 终态同样 409
    cid2 = await _seed_conversation(factory, user_id="u2")
    ticket2 = await _make_ticket(factory, cid2)
    await ac.post(f"/api/v1/interventions/{ticket2}/escalate", json={"resolved_by": "agent-02"})
    resp = await ac.post(
        f"/api/v1/interventions/{ticket2}/resolve",
        json={"resolution_note": "补处理", "resolved_by": "agent-02"},
    )
    assert resp.status_code == 409


async def test_resolve_validation(client) -> None:
    """resolve 必填结论：空 note 422。"""
    ac, factory = client
    cid = await _seed_conversation(factory)
    ticket_id = await _make_ticket(factory, cid)
    resp = await ac.post(
        f"/api/v1/interventions/{ticket_id}/resolve",
        json={"resolution_note": "", "resolved_by": "agent-01"},
    )
    assert resp.status_code == 422
