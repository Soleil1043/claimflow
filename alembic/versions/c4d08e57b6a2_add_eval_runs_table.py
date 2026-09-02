"""add eval_runs table

Revision ID: c4d08e57b6a2
Revises: a8e85d881b28
Create Date: 2026-09-02 21:00:00.000000

评测运行历史（T052，D028）：UI/CLI 运行统一落库，含 git_sha 与趋势冗余率列。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from sqlalchemy import Text

# revision identifiers, used by Alembic.
revision: str = "c4d08e57b6a2"
down_revision: Union[str, Sequence[str], None] = "a8e85d881b28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "eval_runs",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="ui"),
        sa.Column("dataset", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("variant", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=32), nullable=True),
        sa.Column("run_limit", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("return_code", sa.Integer(), nullable=True),
        sa.Column("git_sha", sa.String(length=40), nullable=False, server_default="unknown"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("task_completion_rate", sa.Numeric(6, 4), nullable=True),
        sa.Column("tool_accuracy", sa.Numeric(6, 4), nullable=True),
        sa.Column("report_name", sa.String(length=255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "summary",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("log_tail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("eval_runs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_eval_runs_run_id"), ["run_id"], unique=True)
        batch_op.create_index(batch_op.f("ix_eval_runs_status"), ["status"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("eval_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_eval_runs_status"))
        batch_op.drop_index(batch_op.f("ix_eval_runs_run_id"))

    op.drop_table("eval_runs")
