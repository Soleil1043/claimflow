"""核赔案件 API 测试（T080）：提交/幂等/详情/材料上传。

文件 SQLite（tmp_path）+ 真实核赔桩图（零 LLM）+ mock 材料提取服务。
注意：必须用文件库而非 :memory:——核赔图内并行 worker 的 recorder 会话与 API 请求
会话共享 StaticPool 单连接时事务互相污染（实测丢审计事件），文件库各连接独立。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.api.v1.cases as cases_module
import services.db.session as session_module
from app.main import app
from services.db.models import Base, Policy
from services.materials import MaterialExtraction, detect_material_type
from workflows.case_graph import create_default_case_graph


@pytest.fixture()
async def client(monkeypatch, tmp_path: Path):
    """文件 SQLite + 种子保单 + 真实核赔桩图 + mock 提取服务 + 测试客户端。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'cases_test.db').as_posix()}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "_engine", engine)
    monkeypatch.setattr(session_module, "_session_factory", factory)
    monkeypatch.setattr(session_module.settings, "llm_api_key", "sk-test")
    # T081：API 测试保持确定性编排（零 LLM）；LLM 路由一致性见 verify_orchestrator 脚本
    monkeypatch.setattr(session_module.settings, "orchestrator_llm_enabled", False)
    monkeypatch.setattr(session_module.settings, "material_review_llm_enabled", False)
    monkeypatch.setattr(session_module.settings, "liability_llm_enabled", False)
    monkeypatch.setattr(session_module.settings, "decision_writer_llm_enabled", False)

    async with factory() as s:
        s.add_all(
            [
                Policy(
                    policy_no="POL-2025-0001",
                    holder_name="张伟",
                    holder_id_card="330106199203154817",
                    product_name="安心医疗保险（旗舰版）",
                    product_type="医疗险",
                    coverage_amount=Decimal("1000000.00"),
                    deductible=Decimal("10000.00"),
                    payout_ratio=Decimal("0.8000"),
                    effective_date=dt.date(2025, 1, 1),
                    expiry_date=dt.date(2026, 12, 31),
                    status="active",
                ),
                Policy(
                    policy_no="POL-2023-0004",
                    holder_name="陈静",
                    holder_id_card="330104199001013328",
                    product_name="出行无忧意外伤害保险",
                    product_type="意外险",
                    coverage_amount=Decimal("200000.00"),
                    deductible=Decimal("0.00"),
                    payout_ratio=Decimal("0.9000"),
                    effective_date=dt.date(2023, 8, 15),
                    expiry_date=dt.date(2026, 8, 14),
                    status="active",
                ),
            ]
        )
        await s.commit()

    # 真实核赔桩图（零 LLM；recorder/查询走 DB 实现打文件库）
    monkeypatch.setattr(
        app.state, "case_graph", create_default_case_graph(), raising=False
    )
    # T103 交付队列：inline 派发器（dispatch 同步执行，保留 19 用例的同步终态语义）
    from services.case_jobs import InlineDispatcher
    from services.case_store import DbCaseRecorder

    monkeypatch.setattr(
        app.state,
        "case_dispatcher",
        InlineDispatcher(app.state.case_graph, DbCaseRecorder()),
        raising=False,
    )
    monkeypatch.setattr(session_module.settings, "case_jobs_execution", "inline")

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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await engine.dispose()


def _body(**overrides):
    """标准自动签发案（POL-2025-0001，15800 → 4640）。"""
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


async def test_submit_auto_issued_and_detail(client: AsyncClient) -> None:
    """自动签发主链路：201 终态结论 + 决定书；详情含审计时间线与七个阶段事件。"""
    resp = await client.post("/api/v1/cases", json=_body())
    assert resp.status_code == 201
    body = resp.json()
    assert body["idempotent"] is False
    assert body["case_type"] == "medical"
    assert body["status"] == "auto_issued"
    assert body["final_decision"] == "approved"
    assert Decimal(str(body["approved_amount"])) == Decimal("4640.00")
    assert body["decision_document"]["conclusion"] == "approved"
    assert body["human"] is None

    detail = (await client.get(f"/api/v1/cases/{body['case_id']}")).json()
    assert detail["status"] == "auto_issued"
    assert len(detail["materials"]) == 3
    assert detail["decision_document"]["body"]
    kinds = {e["kind"] for e in detail["timeline"]}
    assert {"routing", "stage_result", "status_change"} <= kinds
    stages = {
        e["stage"] for e in detail["timeline"] if e["kind"] == "stage_result"
    }
    assert stages >= {"intake", "material_review", "policy_verify", "fraud_check",
                      "liability_judge", "amount_calc", "decision_generate",
                      "compliance_gate"}
    # 时间线 seq 升序（回放口径）
    seqs = [e["seq"] for e in detail["timeline"]]
    assert seqs == sorted(seqs)


async def test_submit_idempotent_natural_key(client: AsyncClient) -> None:
    """自然键重复提交 → 200 + 既有案件（不重复执行）；不同金额 → 新案件。"""
    first = await client.post("/api/v1/cases", json=_body())
    assert first.status_code == 201
    case_id = first.json()["case_id"]

    second = await client.post("/api/v1/cases", json=_body())
    assert second.status_code == 200
    body = second.json()
    assert body["idempotent"] is True
    assert body["case_id"] == case_id

    third = await client.post(
        "/api/v1/cases", json=_body(claimed_amount="13500.00")
    )
    assert third.status_code == 201
    assert third.json()["case_id"] != case_id


async def test_submit_offline_line_referred(client: AsyncClient) -> None:
    """未上线险种（意外险）→ 受理期转人工（escape）。"""
    resp = await client.post(
        "/api/v1/cases",
        json=_body(
            user_id="u-chenjing",
            policy_no="POL-2023-0004",
            claimed_amount="8600.00",
            incident_date="2026-08-25",
            incident_description="雨天摔倒致手腕骨折，费用8600元。",
            declared_case_type="accident",
        ),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "referred"
    assert body["case_type"] == "accident"
    assert body["human"] is not None and body["human"]["kind"] == "escape"
    assert body["final_decision"] is None


async def test_submit_missing_material_supplement(client: AsyncClient) -> None:
    """缺费用清单 → 补件挂起，缺失清单精确。"""
    materials = _body()["materials"][:2]  # 去掉 cost_list
    resp = await client.post("/api/v1/cases", json=_body(materials=materials))
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "supplement_pending"
    assert body["human"] is not None and body["human"]["kind"] == "supplement"
    assert body["human"]["missing"] == ["费用清单"]


async def test_get_case_404(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/cases/CASE-2099-9999")
    assert resp.status_code == 404


async def test_create_validation_422(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/cases", json=_body(claimed_amount="0"))
    assert resp.status_code == 422


async def test_upload_material_appends_to_case(client: AsyncClient) -> None:
    """B03 材料上传：提取结果落案件档案（materials+1）并产审计事件。"""
    submitted = await client.post(
        "/api/v1/cases",
        json=_body(materials=[{"file_name": "invoice.jpg", "doc_type": "invoice"}]),
    )
    assert submitted.status_code == 201
    case_id = submitted.json()["case_id"]
    assert submitted.json()["status"] == "supplement_pending"

    resp = await client.post(
        f"/api/v1/cases/{case_id}/materials",
        files={"file": ("诊断证明.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"doc_type": "diagnosis"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["file_type"] == "pdf"
    assert body["doc_type"] == "diagnosis"
    assert body["source"] == "text_model"
    assert body["materials_count"] == 2

    detail = (await client.get(f"/api/v1/cases/{case_id}")).json()
    assert len(detail["materials"]) == 2
    added = detail["materials"][-1]
    assert added["doc_type"] == "diagnosis"
    assert added["extraction"]["diagnosis"] == "急性阑尾炎"
    assert any(e["kind"] == "material_upload" for e in detail["timeline"])

    # T095 seq 单一分配器回归：API 直写事件后图内继续追加，seq 不得重号
    seqs = [e["seq"] for e in detail["timeline"]]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs)), f"seq 重号/乱序: {seqs}"


async def test_upload_material_invalid_type_422(client: AsyncClient) -> None:
    submitted = await client.post("/api/v1/cases", json=_body())
    case_id = submitted.json()["case_id"]
    resp = await client.post(
        f"/api/v1/cases/{case_id}/materials",
        files={"file": ("virus.exe", b"MZ", "application/x-msdownload")},
    )
    assert resp.status_code == 422


async def test_upload_material_unknown_case_404(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/cases/CASE-2099-9999/materials",
        files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert resp.status_code == 404


async def test_submit_background_mode_reaches_terminal(client, monkeypatch) -> None:
    """T103 background 档（生产默认）全回路：受理即返回 + 常驻循环消费 + 轮询终态。"""
    import asyncio

    from services.case_jobs import BackgroundDispatcher, JobLoop
    from services.case_store import DbCaseRecorder

    loop = JobLoop(app.state.case_graph, DbCaseRecorder(), poll_interval_s=0.05)
    await loop.start()
    monkeypatch.setattr(
        app.state, "case_dispatcher", BackgroundDispatcher(), raising=False
    )
    try:
        resp = await client.post("/api/v1/cases", json=_body())
        assert resp.status_code == 201
        body = resp.json()
        # 受理快照：非终态 + 交付凭证（不再同步返回结论）
        assert body["status"] in {"received", "in_progress", "auto_issued"}
        assert body["job"] is not None and body["job"]["action"] == "run"
        case_id = body["case_id"]

        detail: dict = {}
        for _ in range(400):  # ≤20s
            detail = (await client.get(f"/api/v1/cases/{case_id}")).json()
            if detail.get("status") == "auto_issued":
                break
            await asyncio.sleep(0.05)
        assert detail["status"] == "auto_issued"
        assert detail["job"]["status"] == "succeeded"
        assert detail["job"]["outcome"] == "completed"
        assert Decimal(str(detail["approved_amount"])) == Decimal("4640.00")
        assert detail["decision_document"] is not None
    finally:
        await loop.stop(timeout_s=5)
