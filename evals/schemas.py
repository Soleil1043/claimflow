"""核赔金样本评测 Pydantic schema（Phase 8 T088，D037/D039）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------- 核赔金样本 ----------


class AdjudicationExpected(BaseModel):
    """核赔案件期望（金额精确到分；路由三值；worker 序列为派发目标按序子集）。"""

    category: str  # normal / rejected / partial / fraud / edge / missing / intake
    route: Literal["auto", "human", "supplement"]
    liability: str | None = None  # covered / not_covered / partial
    approved_amount: str | None = None  # 精确到分（route=auto 时必判）
    expected_worker_sequence: list[str] = Field(default_factory=list)
    note: str = ""


class AdjudicationCase(BaseModel):
    """核赔金样本案件（与 data/mock/cases.json 种子结构一致）。"""

    case_id: str
    user_id: str
    policy_no: str
    declared_case_type: str | None = None
    case_type: str | None = None  # intake 分类后的实际险种
    claimed_amount: str
    incident_date: str
    incident_description: str
    materials: list[dict[str, Any]] = Field(default_factory=list)
    expected: AdjudicationExpected


class AdjudicationDataset(BaseModel):
    """核赔评测数据集（adjudication.json：_meta + cases + frequency_signals）。"""

    meta: dict[str, Any] = Field(default_factory=dict, alias="_meta")
    cases: list[AdjudicationCase]
    # 理赔频率信号（T083/T089 相对天数 → 运行时换算绝对日期入库，
    # 使"近 90 天"期望不随评测执行时间漂移）
    frequency_signals: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"populate_by_name": True}
