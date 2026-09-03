"""add adjudication tables (cases / case_events / decision_documents)

Revision ID: d7f4b8c2a9e1
Revises: c4d08e57b6a2
Create Date: 2026-09-04 10:00:00.000000

核赔案件域表（Phase 8 T078，D037/D039）：
- cases：案件主档（事实态权威来源；checkpoint 仅执行态，D006 原则延续）
- case_events：案件事件审计（append-only，阶段结论 + kind=routing 路由决策）
- decision_documents：理赔决定书（按 case_id + version 版本化）
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7f4b8c2a9e1"
down_revision: str | Sequence[str] | None = "c4d08e57b6a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_column() -> Any:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "cases",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("policy_no", sa.String(length=32), nullable=False),
        sa.Column("case_type", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column(
            "status", sa.String(length=24), nullable=False, server_default="received"
        ),
        sa.Column("claimed_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("approved_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("incident_date", sa.Date(), nullable=False),
        sa.Column("incident_description", sa.Text(), nullable=False),
        sa.Column("final_decision", sa.String(length=16), nullable=True),
        sa.Column("materials", _json_column(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_cases_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_cases_policy_no"), ["policy_no"], unique=False)
        batch_op.create_index(batch_op.f("ix_cases_case_type"), ["case_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_cases_status"), ["status"], unique=False)

    op.create_table(
        "case_events",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        # stage_result（阶段结论）/ routing（orchestrator 决策+守卫修正）/ guard_correction /
        # human（人工动作）/ status_change
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=True),
        # 案件内递增序号（回放排序依据）
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("payload", _json_column(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("case_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_case_events_case_id"), ["case_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_case_events_kind"), ["kind"], unique=False)

    op.create_table(
        "decision_documents",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        # approved / rejected / partial
        sa.Column("conclusion", sa.String(length=16), nullable=False),
        sa.Column("approved_amount", sa.Numeric(12, 2), nullable=True),
        # auto（系统自动签发）/ agent:<id>（坐席签批）
        sa.Column("issued_by", sa.String(length=64), nullable=False, server_default="auto"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "version", name="uq_decision_case_version"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("decision_documents")

    with op.batch_alter_table("case_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_case_events_kind"))
        batch_op.drop_index(batch_op.f("ix_case_events_case_id"))
    op.drop_table("case_events")

    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_cases_status"))
        batch_op.drop_index(batch_op.f("ix_cases_case_type"))
        batch_op.drop_index(batch_op.f("ix_cases_policy_no"))
        batch_op.drop_index(batch_op.f("ix_cases_user_id"))
    op.drop_table("cases")
