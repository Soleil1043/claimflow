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


class SingleAgentOutput(BaseModel):
    """单 Agent 基线终局输出（T160，D074）——response_format 结构化。

    与图版 ClaimCaseState 的终局字段对齐：route=auto 时 approved_amount 必填；
    route=refer 时 refer_kind 区分 review / supplement（映射 human / supplement）。
    """

    case_type: str | None = None  # medical / auto / property / accident / unknown
    route: Literal["auto", "refer"]
    refer_kind: Literal["review", "supplement"] | None = None
    liability: Literal["covered", "partial", "not_covered"] | None = None
    approved_amount: str | None = None  # 精确到分；非 auto 留空
    decision_summary: str = ""  # 决定书正文叙述（红线检查对象）


# ---------- RAG 评测金样本（T161，D075） ----------


class RagQACase(BaseModel):
    """责任认定 RAG 评测对：gold 用 source_file + 逐字标记子串钉住唯一知识块。

    gold_marker 必须逐字存在于 data/kb_docs/<gold_source_file>（单测强制校验，
    防止文档改写后 gold 静默失配）。
    """

    id: str
    query: str
    gold_source_file: str
    gold_marker: str
    gold_answer: str  # RAGAS context_recall 的归因基准
    category: str = "coverage"


class RagQADataset(BaseModel):
    """RAG 评测数据集（rag_qa_liability.json：_meta + cases）。"""

    meta: dict[str, Any] = Field(default_factory=dict, alias="_meta")
    cases: list[RagQACase]

    model_config = {"populate_by_name": True}


# ---------- 客服问答金样本（T146，D066） ----------


class SupportQACase(BaseModel):
    """客服问答金样本案：期望三层判分（关键词组 / 禁止词 / 转人工终态）。

    - expected_keyword_groups：组间 AND、组内任一命中（同义表述容错）
    - forbidden_keywords：任一出现即失败（红线漏放）
    - expect_escalation：终态 status 是否应为 escalated
    - expected_rag_sources：检索断言——search_kb top-k 的 source_file 与期望
      交集非空即过（任一命中，宽松口径；语料小不做全包含硬门）
    """

    case_id: str
    category: Literal["knowledge", "progress", "redline", "out_of_kb", "escalate"]
    question: str
    expected_keyword_groups: list[list[str]] = Field(default_factory=list)
    forbidden_keywords: list[str] = Field(default_factory=list)
    expect_escalation: bool = False
    expected_rag_sources: list[str] = Field(default_factory=list)
    note: str = ""


class SupportQADataset(BaseModel):
    """客服问答数据集（support_qa.json：_meta + cases）。"""

    meta: dict[str, Any] = Field(default_factory=dict, alias="_meta")
    cases: list[SupportQACase]

    model_config = {"populate_by_name": True}
