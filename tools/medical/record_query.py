"""就诊记录查询工具（T016，F09）。

按身份证号查询就诊记录（诊断、处方、检查结果），
数据来源：medical_records 表（seed 从 data/mock/medical_records.json 入库）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from schemas.tools import ToolInput, ToolOutput
from services.db.models import MedicalRecord
from services.db.session import get_session_factory
from tools.base import BaseTool


class RecordQueryInput(ToolInput):
    """就诊记录查询入参。"""

    id_card: str


class RecordQueryOutput(ToolOutput):
    """查询输出：data.records 为就诊记录列表。"""


class RecordQueryTool(BaseTool[RecordQueryInput, RecordQueryOutput]):
    name = "record_query"
    description = (
        "根据身份证号查询用户的就诊记录，返回医院、科室、诊断、ICD-10 编码、"
        "就诊日期、治疗方式与费用。用户提到'我看过病''我的就诊记录''我做手术'时使用。"
    )
    input_schema = RecordQueryInput
    output_schema = RecordQueryOutput

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        """可注入会话工厂（测试用）。"""
        self._session_factory = session_factory

    def _factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory or get_session_factory()

    async def _run(self, input_data: RecordQueryInput) -> RecordQueryOutput:
        async with self._factory()() as session:
            rows = (
                (
                    await session.execute(
                        select(MedicalRecord)
                        .where(MedicalRecord.patient_id_card == input_data.id_card)
                        .order_by(MedicalRecord.visit_date.desc())
                    )
                )
                .scalars()
                .all()
            )

        if not rows:
            return RecordQueryOutput(
                success=False,
                error_message=f"未找到就诊记录（身份证: {input_data.id_card}）",
            )

        records = [self._to_dict(r) for r in rows]
        return RecordQueryOutput(success=True, data={"records": records})

    @staticmethod
    def _to_dict(r: MedicalRecord) -> dict[str, Any]:
        return {
            "hospital": r.hospital,
            "department": r.department,
            "diagnosis_desc": r.diagnosis_desc,
            "icd10_code": r.icd10_code,
            "visit_date": r.visit_date.isoformat(),
            "treatment": r.treatment,
            "total_amount": float(r.total_amount),
        }
