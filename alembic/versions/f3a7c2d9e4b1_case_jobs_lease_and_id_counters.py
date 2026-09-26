"""case_jobs 租约列 + 案件号计数表（T155，D071 多实例横向扩展）

Revision ID: f3a7c2d9e4b1
Revises: b7d2e6f9a3c1
Create Date: 2026-09-26 11:00:00.000000

- case_jobs 加 locked_by / lease_expires_at：认领归属 + 过期接管（D044 预留升级位兑现；
  存量 running 行 lease 为 NULL = 旧版本孤儿，认领谓词视同过期可回收，兼容滚动升级）
- case_id_counters：案件号年度计数行，upsert-returning 原子自增（跨实例并发不撞号）；
  不做存量播种——生成语句的 INSERT 分支从既有 cases 懒自播种（新旧库同一条路径）
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a7c2d9e4b1"
down_revision: str | Sequence[str] | None = "b7d2e6f9a3c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("case_jobs") as batch:
        batch.add_column(sa.Column("locked_by", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
    op.create_table(
        "case_id_counters",
        sa.Column("year", sa.Integer(), primary_key=True),
        sa.Column("last_no", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("case_id_counters")
    with op.batch_alter_table("case_jobs") as batch:
        batch.drop_column("lease_expires_at")
        batch.drop_column("locked_by")
