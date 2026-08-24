"""医疗审核工具测试（T016，F09 验收）。

- record_query：按身份证查询（多记录倒序）/ 无记录 / schema
- diagnosis_matcher：关键词匹配 / 显式编码 / 未知诊断 / 等待期计算（等待期内+已过）
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.db.models import Base, MedicalRecord
from tools.medical.diagnosis_matcher import DiagnosisMatcherTool
from tools.medical.record_query import RecordQueryTool


@pytest.fixture()
async def record_tool():
    """内存库 + 预置张伟两条就诊记录。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add_all(
            [
                MedicalRecord(
                    patient_id_card="330106199203154817",
                    hospital="杭州市第一人民医院",
                    department="普外科",
                    diagnosis_desc="急性阑尾炎",
                    icd10_code="K35",
                    visit_date=dt.date(2026, 8, 10),
                    treatment="住院手术",
                    total_amount=Decimal("15800.00"),
                ),
                MedicalRecord(
                    patient_id_card="330106199203154817",
                    hospital="浙江大学医学院附属第二医院",
                    department="消化内科",
                    diagnosis_desc="慢性浅表性胃炎",
                    icd10_code="K29.3",
                    visit_date=dt.date(2026, 2, 15),
                    treatment="门诊",
                    total_amount=Decimal("860.00"),
                ),
            ]
        )
        await session.commit()

    yield RecordQueryTool(session_factory=factory)
    await engine.dispose()


# ---------- record_query ----------


async def test_record_query_returns_records_desc(record_tool: RecordQueryTool) -> None:
    """按身份证查询：返回两条记录，按就诊日期倒序。"""
    result = await record_tool.execute({"id_card": "330106199203154817"})
    assert result.success is True
    records = result.data["records"]
    assert len(records) == 2
    assert records[0]["diagnosis_desc"] == "急性阑尾炎"  # 最新在前
    assert records[0]["icd10_code"] == "K35"
    assert records[0]["total_amount"] == 15800.0
    assert records[1]["diagnosis_desc"] == "慢性浅表性胃炎"


async def test_record_query_no_records(record_tool: RecordQueryTool) -> None:
    """无就诊记录：success=False。"""
    result = await record_tool.execute({"id_card": "330000000000000000"})
    assert result.success is False
    assert "未找到就诊记录" in (result.error_message or "")


# ---------- diagnosis_matcher ----------


@pytest.fixture()
def matcher_tool() -> DiagnosisMatcherTool:
    return DiagnosisMatcherTool()


async def test_matcher_appendicitis_covered(matcher_tool: DiagnosisMatcherTool) -> None:
    """F09 验收主用例：'急性阑尾炎' → K35 + 保障范围结论。"""
    result = await matcher_tool.execute({"diagnosis_desc": "急性阑尾炎"})
    assert result.success is True
    data = result.data
    assert data["icd10_code"] == "K35"
    assert data["diagnosis_name"] == "急性阑尾炎"
    assert data["covered"] is True
    assert "住院医疗" in data["coverage_note"]


async def test_matcher_explicit_code(matcher_tool: DiagnosisMatcherTool) -> None:
    """描述中含显式编码（'急性阑尾炎 K35'）：直接识别。"""
    result = await matcher_tool.execute({"diagnosis_desc": "急性阑尾炎 K35"})
    assert result.data["icd10_code"] == "K35"


async def test_matcher_unknown_diagnosis(matcher_tool: DiagnosisMatcherTool) -> None:
    """未知诊断：covered=None 提示人工核对（不报错）。"""
    result = await matcher_tool.execute({"diagnosis_desc": "罕见综合征XYZ"})
    assert result.success is True
    assert result.data["icd10_code"] is None
    assert result.data["covered"] is None
    assert "人工核对" in result.data["coverage_note"]


async def test_matcher_waiting_period_in(matcher_tool: DiagnosisMatcherTool) -> None:
    """等待期内：保单 08-01 生效 + 08-20 就诊 → 19 天 < 30 天，in_waiting_period=True。"""
    result = await matcher_tool.execute(
        {
            "diagnosis_desc": "急性阑尾炎",
            "visit_date": "2026-08-20",
            "policy_effective_date": "2026-08-01",
        }
    )
    waiting = result.data["waiting_period"]
    assert waiting is not None
    assert waiting["in_waiting_period"] is True
    assert waiting["days_since_effective"] == 19
    assert "等待期内" in waiting["note"]


async def test_matcher_waiting_period_passed(matcher_tool: DiagnosisMatcherTool) -> None:
    """等待期已过：保单 2025-01-01 生效 + 2026-08-10 就诊 → 已过 30 天。"""
    result = await matcher_tool.execute(
        {
            "diagnosis_desc": "急性阑尾炎",
            "visit_date": "2026-08-10",
            "policy_effective_date": "2025-01-01",
        }
    )
    waiting = result.data["waiting_period"]
    assert waiting["in_waiting_period"] is False
    assert "已过" in waiting["note"]


async def test_matcher_no_dates_skips_waiting(matcher_tool: DiagnosisMatcherTool) -> None:
    """不提供日期：waiting_period=None（跳过等待期计算）。"""
    result = await matcher_tool.execute({"diagnosis_desc": "肺炎"})
    assert result.data["waiting_period"] is None


def test_matcher_openai_schema() -> None:
    """schema 符合 function calling 格式。"""
    definition = DiagnosisMatcherTool().to_openai_tool()
    fn = definition["function"]
    assert fn["name"] == "diagnosis_matcher"
    assert "ICD-10" in fn["description"]
    assert "diagnosis_desc" in fn["parameters"]["properties"]


def test_record_query_openai_schema() -> None:
    definition = RecordQueryTool().to_openai_tool()
    assert definition["function"]["name"] == "record_query"
