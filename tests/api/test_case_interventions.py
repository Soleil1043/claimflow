"""核赔工单 API 测试（T086）：三类工单列表/处理/红线复审/补件自动恢复/跨重启。"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver

import app.api.v1.cases as cases_module
import nodes.material_review as mr_module
import services.db.session as session_module
from app.main import app
from services.case_store import DbCaseRecorder
from services.db.session import dispose_engine
from services.materials import MaterialExtraction, detect_material_type
from workflows.case_graph import build_case_graph, db_fraud_lookup, db_policy_lookup


@pytest.fixture()
async def env(monkeypatch, tmp_path: Path):
    """公共内核（T109）+ 显式共享 saver（跨重启模拟）+ 可重建图 + mock 提取。"""
    from tests.conftest import make_case_api_core

    engine, factory, seed = await make_case_api_core(
        monkeypatch, tmp_path, db_name="t.db"
    )
    await seed()

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
    # T103：inline 派发器（同步终态语义；跨重启测试换图后需重挂）
    from services.case_jobs import InlineDispatcher

    def _mount_dispatcher() -> None:
        monkeypatch.setattr(
            app.state, "case_dispatcher",
            InlineDispatcher(app.state.case_graph, DbCaseRecorder()), raising=False,
        )

    _mount_dispatcher()
    monkeypatch.setattr(session_module.settings, "case_jobs_execution", "inline")

    async def fake_extract(filename: str, mime: str, content: bytes):
        return MaterialExtraction(
            patient_name="张三", diagnosis="急性阑尾炎", amount=15800.0,
            date="2026-08-10", source="text_model",
            file_type=detect_material_type(filename, mime),
        )

    monkeypatch.setattr(cases_module, "extract_material", fake_extract)
    monkeypatch.setattr(mr_module, "extract_material", fake_extract)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, build
    await dispose_engine()  # T110：复位全局（dispose+置 None）


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
    # D046：未签发——草稿可见（坐席复核视图）但 decision_issued=False
    assert body["decision_issued"] is False


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
    import services.case_jobs as case_jobs_module

    app.state.case_graph = build()
    app.state.case_dispatcher = case_jobs_module.InlineDispatcher(
        app.state.case_graph, DbCaseRecorder()
    )
    resolved = await ac.post(
        f"/api/v1/interventions/cases/{case_id}/resolve",
        json={"action": "confirm", "note": "重启后恢复复核", "resolved_by": "agent-09"},
    )
    assert resolved.status_code == 200
    body = resolved.json()
    assert body["status"] == "closed"
    assert body["final_decision"] == "approved"


async def test_intervention_kind_single_source_from_receipt(env) -> None:
    """D047 挂起信息单源：kind 从交付回执读，不再猜状态。

    - escape 案（未上线险种受理转人工）：kind=escape（旧状态猜测会误标 review）
    - 列表与 resolve 读同一回执；补件上传恢复后 kind 随新回执更新
    """
    ac, _ = env
    offline = {
        "user_id": "u-kind-source",
        "policy_no": "POL-2023-0004",
        "claimed_amount": "8600.00",
        "incident_date": "2026-08-25",
        "incident_description": "雨天摔倒骨折，费用8600元。",
        "declared_case_type": "accident",
        "materials": [],
    }
    resp = await ac.post("/api/v1/cases", json=offline)
    case_id = resp.json()["case_id"]

    listing = (await ac.get("/api/v1/interventions/cases")).json()
    item = next(i for i in listing["items"] if i["case_id"] == case_id)
    assert item["human"]["kind"] == "escape", "回执单源：escape 不应被状态猜测误标 review"

    resolved = await ac.post(
        f"/api/v1/interventions/cases/{case_id}/resolve",
        json={"note": "转专家线下", "resolved_by": "agent-09"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "referred"
