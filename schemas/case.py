"""核赔案件图级输入/输出模型（Phase 8，v2 架构文档第五节）。

CaseInput 作主图 input_schema、CaseOutput 作 output_schema（字段与 ClaimCaseState 同名映射）；
API 层请求/响应模型在 T080（schemas/api.py）基于此复用。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from schemas.stages import DecisionDocOutput, FinalDecision, InsuranceLine

# 案件状态机（cases.status）：
# received → in_progress →（supplement_pending 补件挂起）→ auto_issued / referred / closed
CaseStatus = Literal[
    "received", "in_progress", "supplement_pending", "auto_issued", "referred", "closed"
]


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


class CaseOutput(BaseModel):
    """案件终态出参（graph output_schema）。"""

    case_id: str
    case_type: InsuranceLine
    final_decision: FinalDecision | None = None
    approved_amount: Decimal | None = None
    decision_document: DecisionDocOutput | None = None
