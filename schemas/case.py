"""核赔案件图级输入/输出模型（Phase 8，v2 架构文档第五节）。

CaseInput 作主图 input_schema、CaseOutput 作 output_schema（字段与 ClaimCaseState 同名映射）；
API 层请求/响应模型在 T080（schemas/api.py）基于此复用。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from schemas.stages import DecisionDocOutput, FinalDecision, InsuranceLine


class CaseStatus(StrEnum):
    """案件状态机（cases.status，T095 归一：写入点全部引用枚举成员）。

    received → in_progress →（supplement_pending 补件挂起）→ auto_issued / referred / closed
    """

    RECEIVED = "received"
    IN_PROGRESS = "in_progress"
    SUPPLEMENT_PENDING = "supplement_pending"
    AUTO_ISSUED = "auto_issued"
    REFERRED = "referred"
    CLOSED = "closed"


# 工单挂起态（interventions 工单列表/处理守卫口径）
PENDING_CASE_STATUSES: tuple[CaseStatus, ...] = (
    CaseStatus.SUPPLEMENT_PENDING,
    CaseStatus.REFERRED,
)


class CaseMaterialRef(BaseModel):
    """提交时的材料引用（上传后由 API 填充落盘路径；Mock 场景可只有文件名）。"""

    file_name: str
    # 客户可声明材料类型；intake/材料审核阶段可校正
    doc_type: Literal["invoice", "diagnosis", "cost_list", "medical_record"] | None = None
    storage_path: str | None = None


class CaseInput(BaseModel):
    """案件受理入参。"""

    case_id: str                      # 业务案件号（服务端生成，CASE-YYYY-NNNN）
    user_id: str
    policy_id: str                    # 保单号（policies.policy_no）
    claimed_amount: Decimal
    incident_date: date
    incident_description: str
    # 客户自报险种（可空）；以 intake 分类为准（F01）
    declared_case_type: InsuranceLine | None = None
    materials: list[CaseMaterialRef] = Field(default_factory=list)


class CaseInputState(TypedDict, total=False):
    """图 input_schema（TypedDict 版）。

    不用 pydantic CaseInput 作 input_schema：langgraph 会把字段值转成模型对象，
    节点内 materials 等需按 dict 消费；API 层（T080）用 CaseInput 校验后 dump dict 进图。
    """

    case_id: str
    user_id: str
    policy_id: str
    claimed_amount: Decimal
    incident_date: date
    incident_description: str
    declared_case_type: str | None
    materials: list[dict]


class CaseOutput(BaseModel):
    """案件终态出参（graph output_schema）。

    case_type 用 str 而非 InsuranceLine：转人工案件可能是未上线险种（unknown/重疾等），
    不能在出口被枚举校验卡住。
    """

    case_id: str
    case_type: str
    final_decision: FinalDecision | None = None
    approved_amount: Decimal | None = None
    decision_document: DecisionDocOutput | None = None
