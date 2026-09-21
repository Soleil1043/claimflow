"""services/db ORM 模型与会话管理测试。

使用内存 SQLite（aiosqlite）验证建表、CRUD、JSON 字段、UUID 外键。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.db.models import (
    Base,
    Case,
    CaseEvent,
    ClaimRecord,
    DecisionDocument,
    KbDocument,
    MedicalRecord,
    Policy,
)


@pytest.fixture()
async def db_session():
    """每个测试独立的内存库会话。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_all_tables_created(db_session) -> None:
    """11 张业务表全部可建、可查（核赔域 4 张 + 客服域 2 张 + mock/支撑 5 张）。"""
    tables = {t for t in Base.metadata.tables}
    assert tables == {
                        "policies",
        "medical_records",
        "claim_records",
        "kb_documents",
                "eval_runs",
        "cases",
        "case_events",
        "decision_documents",
        "case_jobs",
        "support_conversations",
        "support_messages",
    }
    for table in Base.metadata.tables.values():
        # 每张表均可查询（空表 select 即验证表结构已创建）
        await db_session.execute(select(table))


async def test_policy_numeric_and_date_fields(db_session) -> None:
    """保单：Numeric 精度与 Date 字段往返。"""
    policy = Policy(
        policy_no="POL-2025-0001",
        holder_name="游三",
        holder_id_card="330106199001011234",
        product_name="安心医疗保险（旗舰版）",
        product_type="医疗险",
        coverage_amount=Decimal("1000000.00"),
        deductible=Decimal("10000.00"),
        payout_ratio=Decimal("0.8000"),
        effective_date=dt.date(2025, 1, 1),
        expiry_date=dt.date(2026, 12, 31),
        status="active",
    )
    db_session.add(policy)
    await db_session.flush()

    loaded = (
        await db_session.execute(select(Policy).where(Policy.policy_no == "POL-2025-0001"))
    ).scalar_one()
    assert loaded.coverage_amount == Decimal("1000000.00")
    assert loaded.payout_ratio == Decimal("0.8000")
    assert loaded.effective_date == dt.date(2025, 1, 1)


async def test_medical_record_and_claim_and_kb_document(db_session) -> None:
    """就诊记录 / 理赔申请 / 知识库文档元数据 CRUD。"""
    db_session.add_all(
        [
            MedicalRecord(
                patient_id_card="330106199001011234",
                hospital="杭州市第一人民医院",
                department="普外科",
                diagnosis_desc="急性阑尾炎",
                icd10_code="K35",
                visit_date=dt.date(2026, 8, 1),
                treatment="住院手术",
                total_amount=Decimal("15800.00"),
            ),
            ClaimRecord(
                claim_no="CLM-2026-0001",
                policy_no="POL-2025-0001",
                status="reviewing",
                applied_amount=Decimal("15800.00"),
                approved_amount=None,
                submitted_at=dt.datetime(2026, 8, 2, 10, 0),
                updated_at=dt.datetime(2026, 8, 10, 9, 0),
            ),
            KbDocument(
                title="安心医疗保险理赔规则",
                source_file="claim_rules.md",
                category="理赔规则",
                chunk_count=12,
                embedded_at=dt.datetime(2026, 8, 20),
            ),
        ]
    )
    await db_session.flush()

    assert (await db_session.execute(select(MedicalRecord))).scalar_one().icd10_code == "K35"
    claim = (await db_session.execute(select(ClaimRecord))).scalar_one()
    assert claim.status == "reviewing" and claim.approved_amount is None
    assert (await db_session.execute(select(KbDocument))).scalar_one().chunk_count == 12


async def test_adjudication_tables_crud(db_session) -> None:
    """核赔域三表（T078）：案件主档 + 事件审计 append-only + 决定书版本化。"""
    case = Case(
        id="CASE-2026-0001",
        user_id="u-zhangwei",
        policy_no="POL-2025-0001",
        case_type="unknown",  # intake 分类前为 unknown
        status="received",
        claimed_amount=Decimal("15800.00"),
        incident_date=dt.date(2026, 8, 10),
        incident_description="急性阑尾炎住院手术",
        materials=[{"file_name": "invoice.jpg", "doc_type": "invoice"}],
    )
    db_session.add(case)
    await db_session.flush()

    db_session.add_all(
        [
            CaseEvent(
                case_id=case.id,
                kind="routing",
                stage=None,
                seq=1,
                payload={
                    "decision": {"next": ["material_review"]},
                    "guard_corrected": False,
                    "reason": "案件受理，先审材料",
                },
            ),
            CaseEvent(
                case_id=case.id,
                kind="stage_result",
                stage="material_review",
                seq=2,
                payload={"completeness": "complete", "confidence": 0.97},
            ),
        ]
    )
    db_session.add(
        DecisionDocument(
            case_id=case.id,
            version=1,
            title="理赔决定书",
            body="兹核定赔付 4640.00 元……",
            conclusion="approved",
            approved_amount=Decimal("4640.00"),
            issued_by="auto",
        )
    )
    await db_session.flush()

    loaded_case = (await db_session.execute(select(Case))).scalar_one()
    assert loaded_case.claimed_amount == Decimal("15800.00")
    assert loaded_case.materials[0]["doc_type"] == "invoice"
    assert loaded_case.created_at is not None

    events = (
        (await db_session.execute(select(CaseEvent).order_by(CaseEvent.seq))).scalars().all()
    )
    assert [e.kind for e in events] == ["routing", "stage_result"]
    assert events[0].payload["decision"]["next"] == ["material_review"]

    doc = (await db_session.execute(select(DecisionDocument))).scalar_one()
    assert doc.approved_amount == Decimal("4640.00")
    assert doc.issued_by == "auto"

    # (case_id, version) 唯一：同版本重复出书必须拒错
    db_session.add(
        DecisionDocument(
            case_id=case.id,
            version=1,
            title="重复版本",
            body="x",
            conclusion="approved",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()
