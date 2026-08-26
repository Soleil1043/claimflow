"""数据库 ORM 模型（SQLAlchemy 2.0 声明式）。

表结构见 .agent/plan.md 第 3 节，共 6 张业务表：
conversations / messages / policies / medical_records / claim_records / kb_documents。
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
