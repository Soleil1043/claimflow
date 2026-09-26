"""核赔案件 API 测试（T080）：提交/幂等/详情/材料上传。

文件 SQLite（tmp_path）+ 真实核赔桩图（零 LLM）+ mock 材料提取服务。
注意：必须用文件库而非 :memory:——核赔图内并行 worker 的 recorder 会话与 API 请求
会话共享 StaticPool 单连接时事务互相污染（实测丢审计事件），文件库各连接独立。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import app.api.v1.cases as cases_module
import services.case_jobs as jobs_module
import services.db.session as session_module
from app.main import app
from services.case_jobs import CaseJobConflictError
from services.db.session import dispose_engine
from services.materials import MaterialExtraction, detect_material_type
from workflows.case_graph import create_default_case_graph


@pytest.fixture()
async def client(monkeypatch, tmp_path: Path):
    """文件库 + 种子保单 + 真实核赔桩图 + mock 提取 + 测试客户端（公共内核 T109）。"""
    from services.case_jobs import InlineDispatcher
    from services.case_store import DbCaseRecorder
    from tests.conftest import make_case_api_core

    engine, factory, seed = await make_case_api_core(monkeypatch, tmp_path, db_name="cases_test.db")
    await seed()

    # T081：API 测试保持确定性编排（零 LLM）；LLM 路由一致性见 verify_orchestrator 脚本
    monkeypatch.setattr(session_module.settings, "orchestrator_llm_enabled", False)

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
    # T103 交付队列：inline 派发器（dispatch 同步执行，保留用例的同步终态语义）
    monkeypatch.setattr(
        app.state, "case_dispatcher", InlineDispatcher(graph, DbCaseRecorder()),
        raising=False,
    )
    monkeypatch.setattr(session_module.settings, "case_jobs_execution", "inline")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await dispose_engine()  # T110：复位全局（dispose+置 None）


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


async def test_material_catalog_pack_derived(client: AsyncClient) -> None:
    """材料目录（T126）：四险种分组齐备，与 pack required_docs 单源一致。"""
    resp = await client.get("/api/v1/cases/material-catalog")
    assert resp.status_code == 200
    lines = {entry["line"]: entry for entry in resp.json()["lines"]}
    assert set(lines) == {"medical", "auto", "property", "accident"}
    assert [d["value"] for d in lines["auto"]["docs"]] == [
        "police_report", "repair_invoice", "loss_assessment",
    ]
    assert lines["property"]["label"] == "财产险"


async def test_submit_offline_line_referred(client: AsyncClient) -> None:
    """产品类型不在任何上线 pack（重疾险 → unknown）→ 受理期转人工（escape）。

    T120 后四险种全部上线，离线口径只剩 unknown。
    """
    resp = await client.post(
        "/api/v1/cases",
        json=_body(
            user_id="u-lina",
            policy_no="POL-2025-0002",
            claimed_amount="500000.00",
            incident_date="2026-08-28",
            incident_description="确诊乳腺癌（重疾），申请重大疾病理赔。",
        ),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "referred"
    assert body["case_type"] == "unknown"
    assert body["human"] is not None and body["human"]["kind"] == "escape"
    assert body["final_decision"] is None


async def test_submit_online_line_runs_pipeline(client: AsyncClient) -> None:
    """上线险种（意外险）受理即进管线——补件挂起或自动结论，不再 escape（T120）。"""
    resp = await client.post(
        "/api/v1/cases",
        json=_body(
            user_id="u-chenjing",
            policy_no="POL-2026-0010",
            claimed_amount="3000.00",
            incident_date="2026-07-02",
            incident_description="雨天路滑跌倒致手腕骨折，门诊治疗费用3000元。",
            declared_case_type="accident",
            materials=[
                {"file_name": "incident_proof.jpg", "doc_type": "incident_proof"},
                {"file_name": "diagnosis.jpg", "doc_type": "diagnosis"},
                {"file_name": "invoice.jpg", "doc_type": "invoice"},
            ],
        ),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["case_type"] == "accident"
    assert body["human"] is None or body["human"]["kind"] != "escape"
    assert body["status"] in {"auto_issued", "in_progress"}


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


async def test_upload_material_delivery_conflict_absorbed(client, monkeypatch) -> None:
    """T143 回归：撞活跃 resume 任务（deliver 冲突回滚会话）→ 200 静默吸收，不 500。

    根因：deliver 冲突分支 rollback 复位会话，rollback 无条件使 ORM 实例过期；
    响应构造再读 case.id 触发 async 惰性加载 → MissingGreenlet → 500（连续
    补件上传实测）。修复=deliver 之后只用本地快照（路由参数 case_id + 早取的
    case.status），不再触碰 case 实例。
    """
    submitted = await client.post(
        "/api/v1/cases",
        json=_body(materials=[{"file_name": "invoice.jpg", "doc_type": "invoice"}]),
    )
    assert submitted.status_code == 201
    case_id = submitted.json()["case_id"]
    assert submitted.json()["status"] == "supplement_pending"

    async def conflict_enqueue(session, *, case_id, action, payload, max_attempts=None):
        """模拟活跃任务撞车：deliver 冲突分支 rollback 后返回 None。

        expire_all 显式复现"会话复位 → 实例过期"这一决定性面——生产
        MissingGreenlet 正源于此后触碰 ORM 属性（async 惰性加载）。
        """
        await session.rollback()
        session.expire_all()
        raise CaseJobConflictError(case_id)

    monkeypatch.setattr(jobs_module, "enqueue_case_job", conflict_enqueue)

    resp = await client.post(
        f"/api/v1/cases/{case_id}/materials",
        files={"file": ("费用清单.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"doc_type": "cost_list"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["case_id"] == case_id
    assert body["materials_count"] == 2
    # 静默吸收：未受理故无交付凭证与状态投影（前端继续轮询）
    assert body["job"] is None and body["case_status"] is None

    # 材料/审计在 deliver 前一事务已 commit，回滚不影响上传事实
    detail = (await client.get(f"/api/v1/cases/{case_id}")).json()
    assert len(detail["materials"]) == 2
    assert detail["materials"][-1]["doc_type"] == "cost_list"


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
        for _ in range(600):  # ≤30s（慢 runner 余量）
            detail = (await client.get(f"/api/v1/cases/{case_id}")).json()
            # 必须两者齐活才 break：案件状态由图内写入先落库，任务行 _finish 在
            # ainvoke 返回之后才提交——只看案件状态会踩进中间窗（CI 慢机实锤）
            if detail.get("status") == "auto_issued" and detail.get("job", {}).get(
                "status"
            ) == "succeeded":
                break
            await asyncio.sleep(0.05)
        assert detail["status"] == "auto_issued"
        assert detail["job"]["status"] == "succeeded"
        assert detail["job"]["outcome"] == "completed"
        assert Decimal(str(detail["approved_amount"])) == Decimal("4640.00")
        assert detail["decision_document"] is not None
    finally:
        await loop.stop(timeout_s=5)


async def test_decision_doc_view_issued_vs_draft(client: AsyncClient) -> None:
    """D046 决定书读模型：签发与否由案件状态推导。

    - 挂起复核案（超阈值转人工）：详情返回草稿（decision_document 非空）
      但 decision_issued=False——客户视图不渲染，坐席视图标注草稿
    - auto_issued 案：decision_issued=True，文档即签发物
    """
    # 超阈值 → review 挂起（走完决定书阶段，草稿已落库）
    referred = await client.post(
        "/api/v1/cases", json=_body(claimed_amount="62000.00")
    )
    assert referred.status_code == 201
    rid = referred.json()["case_id"]
    detail = (await client.get(f"/api/v1/cases/{rid}")).json()
    assert detail["status"] == "referred"
    assert detail["decision_document"] is not None, "草稿应在库（坐席复核用）"
    assert detail["decision_issued"] is False

    # 正常签发
    issued = await client.post("/api/v1/cases", json=_body())
    iid = issued.json()["case_id"]
    detail2 = (await client.get(f"/api/v1/cases/{iid}")).json()
    assert detail2["status"] == "auto_issued"
    assert detail2["decision_issued"] is True
    assert detail2["decision_document"]["version"] == 1


async def test_concurrent_submit_unique_case_ids(client: AsyncClient) -> None:
    """并发提交不撞号（T150 回归）：幂等键各异的 10 案并发提交全部 201 且案号唯一。

    修复前实锤：generate_case_id 读-判-写无锁，并发读同 count 生成重复案号，
    后者撞 (case_id, active) 唯一约束落 409"理论不可达"分支（50 案并发 10
    时 25 案成对失败）。修复 = 幂等检查 + 案号生成 + 建档入队全临界区 asyncio.Lock。
    """
    import asyncio

    bodies = [
        _body(user_id=f"u-concurrent-{i}", materials=[]) for i in range(10)
    ]

    async def _submit(body: dict):
        return await client.post("/api/v1/cases", json=body)

    responses = await asyncio.gather(*(_submit(b) for b in bodies))
    codes = [r.status_code for r in responses]
    assert codes == [201] * 10, f"并发提交应全部 201，实得 {codes}"
    case_ids = [r.json()["case_id"] for r in responses]
    assert len(set(case_ids)) == 10, f"案号应唯一，实得 {case_ids}"


async def test_concurrent_same_natural_key_idempotent(client: AsyncClient) -> None:
    """同自然键并发双提交幂等收口（T150）：锁内二次查重，全部返回同一案号。"""
    import asyncio

    body = _body(user_id="u-dup-key", materials=[])

    async def _submit():
        return await client.post("/api/v1/cases", json=body)

    responses = await asyncio.gather(*(_submit() for _ in range(5)))
    codes = sorted(r.status_code for r in responses)
    assert codes[0] in (200, 201), f"应 201/200，实得 {codes}"
    case_ids = {r.json()["case_id"] for r in responses}
    assert len(case_ids) == 1, f"同自然键应收敛为单一案件，实得 {case_ids}"

