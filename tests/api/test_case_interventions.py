"""核赔工单 API 测试（T086）：三类工单列表/处理/红线复审/补件自动恢复/跨重启。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.api.v1.cases as cases_module
import services.db.session as session_module
from app.main import app
from services.case_store import DbCaseRecorder
from services.db.models import Base, Policy
from services.materials import MaterialExtraction, detect_material_type
from workflows.case_graph import build_case_graph, db_fraud_lookup, db_policy_lookup


def _policy_row(policy_no: str, product_type: str) -> Policy:
    return Policy(
        policy_no=policy_no,
        holder_name="张伟",
        holder_id_card="330106199203154817",
        product_name="安心医疗保险（旗舰版）",
        product_type=product_type,
        coverage_amount=Decimal("1000000.00"),
        deductible=Decimal("10000.00"),
        payout_ratio=Decimal("0.8000"),
        effective_date=dt.date(2025, 1, 1),
        expiry_date=dt.date(2026, 12, 31),
        status="active",
    )


@pytest.fixture()
async def env(monkeypatch, tmp_path: Path):
    """文件库 + 显式共享 saver（跨重启模拟）+ 确定性核赔图 + mock 提取。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 't.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "_engine", engine)
    monkeypatch.setattr(session_module, "_session_factory", factory)
    monkeypatch.setattr(session_module.settings, "llm_api_key", "sk-test")
    for flag in ("orchestrator_llm_enabled", "material_review_llm_enabled",
                 "liability_llm_enabled", "decision_writer_llm_enabled"):
        monkeypatch.setattr(session_module.settings, flag, False)

    async with factory() as s:
        s.add(_policy_row("POL-2025-0001", "医疗险"))
        s.add(_policy_row("POL-2023-0004", "意外险"))
        await s.commit()

    saver = InMemorySaver()

    def build() -> Any:
        """构建图（跨重启测试反复调用——共享同一持久化 checkpointer）。"""
        return build_case_graph(
            recorder=DbCaseRecorder(),
            policy_lookup=db_policy_lookup,
            fraud_lookup=db_fraud_lookup,
            checkpointer=saver,
        )

    monkeypatch.setattr(app.state, "case_graph", build(), raising=False)

    async def fake_extract(filename: str, mime: str, content: bytes):
        return MaterialExtraction(
            patient_name="张三", diagnosis="急性阑尾炎", amount=15800.0,
            date="2026-08-10", source="text_model",
            file_type=detect_material_type(filename, mime),
        )

    monkeypatch.setattr(cases_module, "extract_material", fake_extract)
    import nodes.material_review as mr_module
    monkeypatch.setattr(mr_module, "extract_material", fake_extract)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, build
    await engine.dispose()


def _full_body(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
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


async def _submit(ac: AsyncClient, **overrides: Any) -> dict[str, Any]:
    resp = await ac.post("/api/v1/cases", json=_full_body(**overrides))
    assert resp.status_code == 201
    return resp.json()


async def test_intervention_list_and_confirm_issue(env) -> None:
    """工单列表（kind/reason）+ review confirm → 签发既有结论（issued_by=agent）。"""
    ac, _ = env
    high = await _submit(ac, claimed_amount="62000.00",
                         incident_description="骑车摔伤骨折，费用62000元。")
    assert high["status"] == "referred" and high["human"]["kind"] == "review"

    listing = (await ac.get("/api/v1/interventions/cases")).json()
    assert listing["total"] == 1
    item = listing["items"][0]
    assert item["case_id"] == high["case_id"]
    assert item["human"]["kind"] == "review"

    resolved = await ac.post(
        f"/api/v1/interventions/cases/{high['case_id']}/resolve",
        json={"action": "confirm", "note": "超阈值复核通过", "resolved_by": "agent-01"},
    )
    assert resolved.status_code == 200
    body = resolved.json()
    assert body["status"] == "closed"
    assert body["final_decision"] == "approved"
    assert body["decision_document"]["issued_by"] == "agent:agent-01"

    listing = (await ac.get("/api/v1/interventions/cases")).json()
    assert all(i["case_id"] != high["case_id"] for i in listing["items"])


async def test_review_rewrite_red_line_safe_referred(env) -> None:
    """坐席改判正文含红线话术 → 红线复审拦截，安全兜底 referred、不签发文书。"""
    ac, _ = env
    high = await _submit(ac, claimed_amount="62000.00",
                         incident_description="骑车摔伤骨折，费用62000元。")
    case_id = high["case_id"]

    resolved = await ac.post(
        f"/api/v1/interventions/cases/{case_id}/resolve",
        json={"action": "rewrite", "decision": "approved",
              "approved_amount": "49600.00",
              "body": "本公司保证赔付，百分百全额给付。",
              "note": "改判", "resolved_by": "agent-02"},
    )
    assert resolved.status_code == 200
    body = resolved.json()
    assert body["final_decision"] == "referred"  # 红线拦截，安全兜底
    assert body["decision_document"] is None  # 未签发文书


async def test_review_rewrite_override_issues_agent_doc(env) -> None:
    """坐席改判（合法正文）→ 坐席版决定书签发 + 案件关闭。"""
    ac, _ = env
    high = await _submit(ac, claimed_amount="62000.00",
                         incident_description="骑车摔伤骨折，费用62000元。")
    resolved = await ac.post(
        f"/api/v1/interventions/cases/{high['case_id']}/resolve",
        json={"action": "rewrite", "decision": "partial",
              "approved_amount": "40000.00",
              "reason": "部分自费材料扣除", "note": "酌情部分给付",
              "resolved_by": "agent-02"},
    )
    body = resolved.json()
    assert body["status"] == "closed"
    assert body["final_decision"] == "partial"
    assert Decimal(str(body["approved_amount"])) == Decimal("40000.00")
    assert body["decision_document"]["issued_by"] == "agent:agent-02"


async def test_escape_resolve_referred(env) -> None:
    """受理升级（未上线险种）→ resolve 后终态 referred。"""
    ac, _ = env
    acc = await _submit(
        ac,
        user_id="u-chenjing",
        policy_no="POL-2023-0004",
        claimed_amount="8600.00",
        incident_date="2026-08-25",
        incident_description="雨天摔倒骨折，费用8600元。",
        declared_case_type="accident",
        materials=[],
    )
    assert acc["human"]["kind"] == "escape"
    resolved = await ac.post(
        f"/api/v1/interventions/cases/{acc['case_id']}/resolve",
        json={"note": "转意外险专家线下受理", "resolved_by": "agent-03"},
    )
    body = resolved.json()
    assert body["status"] == "referred"
    assert body["final_decision"] == "referred"


async def test_supplement_upload_auto_resumes(env) -> None:
    """补件闭环：缺件挂起 → B03 上传缺失材料 → 自动恢复 → 自动签发 4640。"""
    ac, _ = env
    missing = await _submit(
        ac,
        materials=[
            {"file_name": "invoice.jpg", "doc_type": "invoice"},
            {"file_name": "diagnosis.jpg", "doc_type": "diagnosis"},
        ],
    )
    assert missing["status"] == "supplement_pending"
    case_id = missing["case_id"]

    upload = await ac.post(
        f"/api/v1/cases/{case_id}/materials",
        files={"file": ("cost_list.pdf", b"%PDF-1.4", "application/pdf")},
        data={"doc_type": "cost_list"},
    )
    assert upload.status_code == 200
    assert upload.json()["case_status"] == "auto_issued"

    detail = (await ac.get(f"/api/v1/cases/{case_id}")).json()
    assert detail["status"] == "auto_issued"
    assert Decimal(str(detail["approved_amount"])) == Decimal("4640.00")


async def test_cross_restart_resume(env) -> None:
    """跨重启恢复：挂起后重建图实例（共享持久化 checkpointer），恢复流程完成签发。"""
    ac, build = env
    high = await _submit(ac, claimed_amount="62000.00",
                         incident_description="骑车摔伤骨折，费用62000元。")
    case_id = high["case_id"]
    assert high["human"]["kind"] == "review"

    # 模拟服务重启：替换为新建的图实例（同一持久化 checkpointer，挂起态存活）
    app.state.case_graph = build()
    resolved = await ac.post(
        f"/api/v1/interventions/cases/{case_id}/resolve",
        json={"action": "confirm", "note": "重启后恢复复核", "resolved_by": "agent-09"},
    )
    assert resolved.status_code == 200
    body = resolved.json()
    assert body["status"] == "closed"
    assert body["final_decision"] == "approved"
