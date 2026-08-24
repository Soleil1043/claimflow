"""services/db ORM 模型与会话管理测试。

使用内存 SQLite（aiosqlite）验证建表、CRUD、JSON 字段、UUID 外键。
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.db.models import (
    Base,
    ClaimRecord,
    Conversation,
    KbDocument,
    MedicalRecord,
    Message,
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


async def test_all_six_tables_created(db_session) -> None:
    """6 张业务表全部可建、可查。"""
    tables = {t for t in Base.metadata.tables}
    assert tables == {
        "conversations",
        "messages",
        "policies",
        "medical_records",
        "claim_records",
        "kb_documents",
    }
    for table in Base.metadata.tables.values():
        # 每张表均可查询（空表 select 即验证表结构已创建）
        await db_session.execute(select(table))


async def test_conversation_with_messages(db_session) -> None:
    """会话 + 消息：UUID 主键、外键、JSON 字段往返。"""
    conv = Conversation(user_id="demo-user")
    db_session.add(conv)
    await db_session.flush()

    msg = Message(
        conversation_id=conv.id,
        role="assistant",
        content="预估赔付金额为 792,000 元",
        intent="multi_step",
        tool_trace=[
            {"tool": "policy_query", "input": {"policy_no": "POL-2025-0001"}, "duration_ms": 35},
            {"tool": "claim_calculator", "input": {"amount": 1000000}, "duration_ms": 12},
        ],
        agent_steps=[{"agent": "medical", "status": "done"}, {"agent": "claim", "status": "done"}],
        compliance_status="PASS",
    )
    db_session.add(msg)
    await db_session.flush()

    result = await db_session.execute(select(Message).where(Message.conversation_id == conv.id))
    loaded = result.scalar_one()
    assert loaded.role == "assistant"
    assert loaded.intent == "multi_step"
    assert len(loaded.tool_trace) == 2
    assert loaded.tool_trace[0]["tool"] == "policy_query"
    assert loaded.agent_steps[0]["agent"] == "medical"
    assert loaded.compliance_status == "PASS"
    assert isinstance(loaded.conversation_id, uuid.UUID)
    # server_default 生效
    assert loaded.created_at is not None


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
