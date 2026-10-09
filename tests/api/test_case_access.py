"""案件门户访问令牌测试（T171，D077）：修复 IDOR——案号可枚举且详情/上传原无鉴权。

矩阵：
- 令牌下发：201 一次性返回；幂等命中 200 不再返回（哈希不可逆，设计使然）
- staff_keys 配置后：无令牌 401 / 错令牌 401 / 对令牌 200（详情 + 上传双端点）
- 坐席通道：有效 X-Staff-Key 放行（workbench 零改动）
- dev 开放态：staff_keys 未配置时放行（T147 兼容口径）
- 旧库 NULL 哈希行（迁移前存量/非门户建档）：无令牌可对，仅坐席通道可读
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import app.api.v1.cases as cases_module
import services.db.session as session_module
from app.api import dependencies as deps
from app.main import app
from services.case_jobs import InlineDispatcher
from services.case_service import new_case
from services.case_store import DbCaseRecorder
from services.db.session import dispose_engine
from services.materials import MaterialExtraction, detect_material_type
from tests.conftest import make_case_api_core
from workflows.case_graph import create_default_case_graph

STAFF_KEYS = "alice:key-alice-123"


@pytest.fixture()
async def client(monkeypatch, tmp_path: Path):
    """同 test_cases.py 内核：文件库 + 种子保单 + 确定性桩图 + mock 提取。"""
    engine, factory, seed = await make_case_api_core(
        monkeypatch, tmp_path, db_name="case_access_test.db"
    )
    await seed()

    async def fake_extract(filename: str, mime: str, content: bytes) -> MaterialExtraction:
        return MaterialExtraction(
            patient_name="张三",
            diagnosis="急性阑尾炎",
            amount=15800.0,
            date="2026-08-20",
            source="text_model",
            file_type=detect_material_type(filename, mime),
        )

    monkeypatch.setattr(cases_module, "extract_material", fake_extract)

    graph = create_default_case_graph()
    monkeypatch.setattr(app.state, "case_graph", graph, raising=False)
    monkeypatch.setattr(
        app.state, "case_dispatcher", InlineDispatcher(graph, DbCaseRecorder()),
        raising=False,
    )
    monkeypatch.setattr(session_module.settings, "case_jobs_execution", "inline")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, factory
    await dispose_engine()


def _body(**overrides):
    base = {
        "user_id": "u-zhangwei",
        "policy_no": "POL-2025-0001",
        "claimed_amount": "15800.00",
        "incident_date": "2026-08-10",
        "incident_description": "急性阑尾炎住院手术，共花费15800元。",
        "materials": [
            {"file_name": "invoice.jpg", "doc_type": "invoice"},
            {"file_name": "diagnosis.jpg", "doc_type": "diagnosis"},
            {"file_name": "cost_list.pdf", "doc_type": "cost_list"},
        ],
    }
    base.update(overrides)
    return base


async def _submit(client: AsyncClient, **overrides) -> tuple[str, str | None]:
    """提交案件，返回 (case_id, access_token)。"""
    resp = await client.post("/api/v1/cases", json=_body(**overrides))
    assert resp.status_code == 201
    body = resp.json()
    return body["case_id"], body["access_token"]


class TestTokenIssuance:
    async def test_201_issues_token_once_idempotent_replay_none(self, client):
        """201 下发明文令牌；同自然键幂等重放 200 不再下发。"""
        ac, _ = client
        case_id, token = await _submit(ac)
        assert token is not None and len(token) >= 32

        replay = await ac.post("/api/v1/cases", json=_body())
        assert replay.status_code == 200
        assert replay.json()["idempotent"] is True
        assert replay.json()["access_token"] is None


class TestPortalEnforcement:
    """staff_keys 配置后（prod 恒如此），门户端点凭令牌或坐席 Key 访问。"""

    async def test_detail_without_token_401(self, client, monkeypatch):
        monkeypatch.setattr(deps.settings, "staff_keys", STAFF_KEYS)
        ac, _ = client
        case_id, _ = await _submit(ac, user_id="u-a1")
        assert (
            await ac.get(f"/api/v1/cases/{case_id}")
        ).status_code == 401

    async def test_detail_wrong_token_401(self, client, monkeypatch):
        monkeypatch.setattr(deps.settings, "staff_keys", STAFF_KEYS)
        ac, _ = client
        case_id, _ = await _submit(ac, user_id="u-a2")
        resp = await ac.get(
            f"/api/v1/cases/{case_id}", headers={"X-Case-Token": "forged-token"}
        )
        assert resp.status_code == 401

    async def test_detail_valid_token_200(self, client, monkeypatch):
        monkeypatch.setattr(deps.settings, "staff_keys", STAFF_KEYS)
        ac, _ = client
        case_id, token = await _submit(ac, user_id="u-a3")
        resp = await ac.get(
            f"/api/v1/cases/{case_id}", headers={"X-Case-Token": token}
        )
        assert resp.status_code == 200
        assert resp.json()["case_id"] == case_id

    async def test_upload_requires_token(self, client, monkeypatch):
        monkeypatch.setattr(deps.settings, "staff_keys", STAFF_KEYS)
        ac, _ = client
        # 缺件案 → supplement_pending，上传端点全链可用
        case_id, token = await _submit(
            ac,
            user_id="u-a4",
            materials=[{"file_name": "invoice.jpg", "doc_type": "invoice"}],
        )
        files = {"file": ("诊断证明.pdf", b"%PDF-1.4 fake", "application/pdf")}
        no_token = await ac.post(
            f"/api/v1/cases/{case_id}/materials", files=files,
            data={"doc_type": "diagnosis"},
        )
        assert no_token.status_code == 401

        files2 = {"file": ("诊断证明.pdf", b"%PDF-1.4 fake", "application/pdf")}
        with_token = await ac.post(
            f"/api/v1/cases/{case_id}/materials", files=files2,
            data={"doc_type": "diagnosis"}, headers={"X-Case-Token": token},
        )
        assert with_token.status_code == 200
        assert with_token.json()["materials_count"] == 2

    async def test_staff_key_grants_access_without_case_token(self, client, monkeypatch):
        """坐席通道：有效 X-Staff-Key 读他人案件（workbench 场景）。"""
        monkeypatch.setattr(deps.settings, "staff_keys", STAFF_KEYS)
        ac, _ = client
        case_id, _ = await _submit(ac, user_id="u-a5")
        resp = await ac.get(
            f"/api/v1/cases/{case_id}", headers={"X-Staff-Key": "key-alice-123"}
        )
        assert resp.status_code == 200


class TestDevOpenCompat:
    async def test_dev_open_without_any_header(self, client, monkeypatch):
        """staff_keys 未配置（dev 默认）：无 header 放行（T147 兼容口径）。"""
        monkeypatch.setattr(deps.settings, "staff_keys", "")
        ac, _ = client
        case_id, _ = await _submit(ac, user_id="u-a6")
        assert (
            await ac.get(f"/api/v1/cases/{case_id}")
        ).status_code == 200


class TestLegacyNullHashRow:
    async def test_null_hash_row_staff_only(self, client, monkeypatch, tmp_path):
        """旧库/非门户建档（哈希 NULL）：无令牌可对 → 仅坐席通道可读。"""
        monkeypatch.setattr(deps.settings, "staff_keys", STAFF_KEYS)
        ac, factory = client
        import datetime as dt
        from decimal import Decimal

        from sqlalchemy import select

        from services.db.models import Case
        legacy = new_case(
            case_id="CASE-2024-0001",
            user_id="u-legacy",
            policy_no="POL-2025-0001",
            claimed_amount=Decimal("1000.00"),
            incident_date=dt.date(2026, 8, 1),
            incident_description="迁移前存量案件",
            materials=[],
        )
        async with factory() as s:
            s.add(legacy)
            await s.commit()

        forged = await ac.get(
            "/api/v1/cases/CASE-2024-0001", headers={"X-Case-Token": "anything"}
        )
        assert forged.status_code == 401

        staff = await ac.get(
            "/api/v1/cases/CASE-2024-0001", headers={"X-Staff-Key": "key-alice-123"}
        )
        assert staff.status_code == 200

        # 行内确实无哈希（口径自证）
        async with factory() as s:
            row = (
                await s.execute(select(Case).where(Case.id == "CASE-2024-0001"))
            ).scalar_one()
            assert row.access_token_hash is None
