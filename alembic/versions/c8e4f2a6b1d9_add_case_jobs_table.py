"""case_jobs 交付任务表（T103，D044 混合方案）

Revision ID: c8e4f2a6b1d9
Revises: b5f9c3d7e2a4
Create Date: 2026-09-06 10:00:00.000000

案件管线异步执行的交付凭证（transactional outbox）：任务行与案件建档/材料写入
同事务插入。同案件活跃任务唯一（partial unique index）。无租约/心跳列——
崩溃恢复靠启动期回收 running 孤儿（单实例契约）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8e4f2a6b1d9"
down_revision: str | Sequence[str] | None = "b5f9c3d7e2a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_column() -> sa.Column:
    return sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def _id_column() -> sa.Column:
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "case_jobs",
        sa.Column("id", _id_column(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("payload", _json_column(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("interrupt_payload", _json_column(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("run_after", sa.DateTime(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_case_jobs_case_id", "case_jobs", ["case_id"])
    op.create_index("ix_case_jobs_status", "case_jobs", ["status"])
    op.create_index("ix_case_jobs_run_after", "case_jobs", ["run_after"])
    op.create_index(
        "uq_case_jobs_active",
        "case_jobs",
        ["case_id"],
        unique=True,
        postgresql_where=text("status IN ('queued', 'running')"),
        sqlite_where=text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_case_jobs_active", table_name="case_jobs")
    op.drop_index("ix_case_jobs_run_after", table_name="case_jobs")
    op.drop_index("ix_case_jobs_status", table_name="case_jobs")
    op.drop_index("ix_case_jobs_case_id", table_name="case_jobs")
    op.drop_table("case_jobs")
