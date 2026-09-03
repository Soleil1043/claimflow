"""数据库 ORM 模型（SQLAlchemy 2.0 声明式）。

业务表共 11 张（Phase 8 T078 新增核赔域 3 张）：
conversations / messages / policies / medical_records / claim_records / kb_documents /
human_tickets / eval_runs / cases / case_events / decision_documents。
LangGraph checkpoint 表由 PostgreSQLSaver 自管，不在此建模（D006）。

跨后端兼容：JSONB（PostgreSQL）自动降级 JSON（SQLite dev），Uuid/BigInteger 走 SQLAlchemy
通用类型，dev（aiosqlite）与 prod（asyncpg）共用同一套模型。
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


def _jsonb_or_json() -> Any:
    """PostgreSQL 用 JSONB，其他后端（SQLite dev）用 JSON。"""
    return JSON().with_variant(JSONB(), "postgresql")


def _autoincrement_id() -> Any:
    """自增主键类型。

    SQLite 只有 INTEGER PRIMARY KEY 才走 rowid 自增，BIGINT 不会，
    故在 SQLite 方言下降级为 Integer；PostgreSQL 保持 BIGSERIAL 语义。
    """
    return BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    """声明式基类。"""


class Conversation(Base):
    """会话：id 即 LangGraph thread_id。"""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    # active / closed / transferred（转人工）
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<Conversation {self.id} status={self.status}>"


class Message(Base):
    """消息：业务审计层，含工具轨迹与合规状态（D006）。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(_autoincrement_id(), primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id"), index=True
    )
    # user / assistant
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    # 意图分类结果（assistant 消息）
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 本轮工具调用明细 [{tool, input, output, duration_ms}]
    tool_trace: Mapped[list[dict[str, Any]] | None] = mapped_column(_jsonb_or_json(), nullable=True)
    # 多 Agent 执行计划与各步结果
    agent_steps: Mapped[list[dict[str, Any]] | None] = mapped_column(
        _jsonb_or_json(), nullable=True
    )
    # PASS / MODIFIED / REJECTED
    compliance_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class Policy(Base):
    """保单（Mock 数据，T008 seed 入库）。"""

    __tablename__ = "policies"

    id: Mapped[int] = mapped_column(_autoincrement_id(), primary_key=True, autoincrement=True)
    policy_no: Mapped[str] = mapped_column(String(32), unique=True)
    holder_name: Mapped[str] = mapped_column(String(64))
    holder_id_card: Mapped[str] = mapped_column(String(18), index=True)
    product_name: Mapped[str] = mapped_column(String(128))
    # 医疗险 / 重疾险 / 意外险
    product_type: Mapped[str] = mapped_column(String(32))
    coverage_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    deductible: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    payout_ratio: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    effective_date: Mapped[dt.date] = mapped_column(Date)
    expiry_date: Mapped[dt.date] = mapped_column(Date)
    # active / expired / surrendered
    status: Mapped[str] = mapped_column(String(16))


class MedicalRecord(Base):
    """就诊记录（Mock 数据，T016 seed 入库）。"""

    __tablename__ = "medical_records"

    id: Mapped[int] = mapped_column(_autoincrement_id(), primary_key=True, autoincrement=True)
    patient_id_card: Mapped[str] = mapped_column(String(18), index=True)
    hospital: Mapped[str] = mapped_column(String(64))
    department: Mapped[str] = mapped_column(String(64))
    diagnosis_desc: Mapped[str] = mapped_column(String(256))
    # ICD-10 编码，如 K35（急性阑尾炎）
    icd10_code: Mapped[str] = mapped_column(String(16))
    visit_date: Mapped[dt.date] = mapped_column(Date)
    # 门诊 / 住院手术 等
    treatment: Mapped[str] = mapped_column(String(64))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class ClaimRecord(Base):
    """理赔申请（Mock 数据，T008 seed 入库）。"""

    __tablename__ = "claim_records"

    id: Mapped[int] = mapped_column(_autoincrement_id(), primary_key=True, autoincrement=True)
    claim_no: Mapped[str] = mapped_column(String(32), unique=True)
    # 逻辑外键关联 policies.policy_no
    policy_no: Mapped[str] = mapped_column(String(32), index=True)
    # submitted / reviewing / approved / rejected / paid
    status: Mapped[str] = mapped_column(String(16))
    applied_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    approved_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    submitted_at: Mapped[dt.datetime] = mapped_column(DateTime)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime)


class KbDocument(Base):
    """RAG 知识库文档元数据；向量与 chunk 存 Qdrant（T010）。"""

    __tablename__ = "kb_documents"

    id: Mapped[int] = mapped_column(_autoincrement_id(), primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(128))
    # data/kb_docs/ 相对路径
    source_file: Mapped[str] = mapped_column(String(256), unique=True)
    # 条款 / 理赔规则 / 免责说明 / 常见问题
    category: Mapped[str] = mapped_column(String(32))
    chunk_count: Mapped[int] = mapped_column(Integer)
    embedded_at: Mapped[dt.datetime] = mapped_column(DateTime)


class HumanTicket(Base):
    """人工介入工单（T036）：REJECT 转人工事件的坐席处理队列。

    状态机：pending → resolved（坐席解决回写结论）/ transferred_out（升级转出），终态不可再流转。
    一会话最多一张 open（pending）工单——重复转人工幂等跳过（ensure_human_ticket 保证）。
    """

    __tablename__ = "human_tickets"

    id: Mapped[int] = mapped_column(_autoincrement_id(), primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[str] = mapped_column(String(64))
    # 拦截原因快照（转人工那一刻的 intervention_reason）
    intervention_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 合规裁决快照（verdict/violations/risk_score/reason 完整结构，聚合上下文展示用）
    compliance_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        _jsonb_or_json(), nullable=True
    )
    # pending / resolved / transferred_out
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # 坐席回写结论（resolve 时必填）
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<HumanTicket {self.id} conversation={self.conversation_id} status={self.status}>"


class EvalRunRecord(Base):
    """评测运行历史（T052，D028）：重启后可回溯，趋势图（T053）主要数据源。

    单写者原则：UI 托管运行由 EvalRunManager 两阶段写（start=running / finish=终态）；
    CLI 运行由 evals.test_suite 进程收尾自记（EVAL_MANAGED_BY=api 时跳过防双写）。
    """

    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(_autoincrement_id(), primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # ui（EvalRunManager 托管）/ cli（命令行直接运行）
    source: Mapped[str] = mapped_column(String(16), default="ui")
    dataset: Mapped[str] = mapped_column(String(32), default="")
    variant: Mapped[str] = mapped_column(String(64), default="")
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    run_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # running / completed / failed
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    return_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    git_sha: Mapped[str] = mapped_column(String(40), default="unknown")
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    # 趋势查询冗余列（来源 summary；SQLite 无 JSON 查询能力，避免整包解析）
    task_completion_rate: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    tool_accuracy: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    report_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 报告 summary 快照（不含 failures 明细）
    summary: Mapped[dict[str, Any] | None] = mapped_column(_jsonb_or_json(), nullable=True)
    # 运行日志末尾（\n 连接，截断保留）
    log_tail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<EvalRunRecord {self.run_id} status={self.status}>"


class Case(Base):
    """核赔案件主档（Phase 8 T078）：案件事实态的权威来源。

    checkpoint 只承载图执行态（可重建）；案件状态以本表为准（D006 原则延续）。
    状态机：received → in_progress →（supplement_pending 补件挂起）→
    auto_issued / referred / closed。
    """

    __tablename__ = "cases"

    # 业务案件号（CASE-YYYY-NNNN），非自增
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    # 逻辑外键 policies.policy_no
    policy_no: Mapped[str] = mapped_column(String(32), index=True)
    # intake 分类写入：medical / auto / property / accident / unknown（未上线险种受理即转人工）
    case_type: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    status: Mapped[str] = mapped_column(String(24), default="received", index=True)
    claimed_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    approved_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    incident_date: Mapped[dt.date] = mapped_column(Date)
    incident_description: Mapped[str] = mapped_column(Text)
    # approved / rejected / partial / referred（签发或转人工时写入）
    final_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 材料引用清单 [{file_name, doc_type, storage_path}]
    materials: Mapped[list[dict[str, Any]] | None] = mapped_column(
        _jsonb_or_json(), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<Case {self.id} type={self.case_type} status={self.status}>"


class CaseEvent(Base):
    """案件事件审计（append-only，只插不改）。

    kind 取值：
    - stage_result：worker 阶段结论（payload=阶段模型 dump）
    - routing：orchestrator 路由决策（payload=原始决策+守卫修正+reason，D039 决策审计）
    - guard_correction：守卫纠错（payload=违规路由与改投结果）
    - human：人工动作（补件/签批/升级）
    - status_change：案件状态流转
    """

    __tablename__ = "case_events"

    id: Mapped[int] = mapped_column(_autoincrement_id(), primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String(64), ForeignKey("cases.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 案件内递增序号（回放排序依据）
    seq: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any] | None] = mapped_column(_jsonb_or_json(), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<CaseEvent {self.id} case={self.case_id} kind={self.kind} seq={self.seq}>"


class DecisionDocument(Base):
    """理赔决定书（版本化，F09）：正文金额与理算结果断言一致（F10 金额断言）。

    版本规则：AI 草稿/修订与坐席改判各占一版，(case_id, version) 唯一。
    """

    __tablename__ = "decision_documents"
    __table_args__ = (UniqueConstraint("case_id", "version", name="uq_decision_case_version"),)

    id: Mapped[int] = mapped_column(_autoincrement_id(), primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String(64), ForeignKey("cases.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(Text)
    # approved / rejected / partial
    conclusion: Mapped[str] = mapped_column(String(16))
    approved_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # auto（系统自动签发）/ agent:<id>（坐席签批）
    issued_by: Mapped[str] = mapped_column(String(64), default="auto")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<DecisionDocument {self.id} case={self.case_id} "
            f"v{self.version} conclusion={self.conclusion}>"
        )
