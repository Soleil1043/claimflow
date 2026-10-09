"""门户限流 API 测试（T172，D078）：POST /cases 与客服消息端点 429 + Retry-After。

conftest autouse 已按测试复位限流单例（内存桶不跨测试累积）。
客服消息用「不存在会话」验证限流先于路由逻辑（404）——不触达 LLM。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import services.db.session as session_module
from app.main import app
from services.db.session import dispose_engine
from tests.conftest import make_case_api_core


@pytest.fixture()
async def client(monkeypatch, tmp_path: Path):
    """案件域内核（同 test_cases.py）+ 客服会话存储真实 SQLite。"""
    engine, factory, seed = await make_case_api_core(
        monkeypatch, tmp_path, db_name="rate_limit_test.db"
    )
    await seed()

    from services.case_jobs import InlineDispatcher
    from services.case_store import DbCaseRecorder
    from workflows.case_graph import create_default_case_graph

    graph = create_default_case_graph()
    monkeypatch.setattr(app.state, "case_graph", graph, raising=False)
    monkeypatch.setattr(
        app.state, "case_dispatcher", InlineDispatcher(graph, DbCaseRecorder()),
        raising=False,
    )
    monkeypatch.setattr(session_module.settings, "case_jobs_execution", "inline")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await dispose_engine()


def _body(idx: int) -> dict:
    return {
        "user_id": f"u-rl-{idx}",
        "policy_no": "POL-2025-0001",
        "claimed_amount": "15800.00",
        "incident_date": "2026-08-10",
        "incident_description": "限流测试案件。",
        "materials": [],
    }


class TestCaseSubmitRateLimit:
    async def test_fourth_submit_429_with_retry_after(self, client, monkeypatch):
        monkeypatch.setattr(session_module.settings, "rate_limit_cases_per_min", 2)
        ac = client
        assert (await ac.post("/api/v1/cases", json=_body(0))).status_code == 201
        assert (await ac.post("/api/v1/cases", json=_body(1))).status_code == 201
        third = await ac.post("/api/v1/cases", json=_body(2))
        assert third.status_code == 429
        assert int(third.headers["Retry-After"]) >= 1

    async def test_zero_quota_unlimited(self, client, monkeypatch):
        """配额 0 = 不限流（负载测试口径）。"""
        monkeypatch.setattr(session_module.settings, "rate_limit_cases_per_min", 0)
        ac = client
        for i in range(5):
            resp = await ac.post("/api/v1/cases", json=_body(10 + i))
            assert resp.status_code == 201, f"第 {i} 案被误限流: {resp.status_code}"


class TestSupportMessageRateLimit:
    async def test_second_message_429(self, client, monkeypatch):
        """配额 1：第一条过限流（404 会话不存在，不触 LLM），第二条 429。"""
        monkeypatch.setattr(session_module.settings, "rate_limit_support_per_min", 1)
        ac = client
        first = await ac.post(
            "/api/v1/support/conversations/conv-rl-test/messages",
            json={"content": "你好"},
        )
        assert first.status_code == 404  # 限流放行 → 路由 404（会话不存在）

        second = await ac.post(
            "/api/v1/support/conversations/conv-rl-test/messages",
            json={"content": "再问一句"},
        )
        assert second.status_code == 429
        assert int(second.headers["Retry-After"]) >= 1

    async def test_cases_quota_independent_of_support(self, client, monkeypatch):
        """域间配额独立：案件配额 1 打满不影响客服消息域。"""
        monkeypatch.setattr(session_module.settings, "rate_limit_cases_per_min", 1)
        ac = client
        assert (await ac.post("/api/v1/cases", json=_body(20))).status_code == 201
        assert (await ac.post("/api/v1/cases", json=_body(21))).status_code == 429
        msg = await ac.post(
            "/api/v1/support/conversations/conv-rl-test/messages",
            json={"content": "你好"},
        )
        assert msg.status_code == 404  # 默认 60/分钟，未受限流影响
