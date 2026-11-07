"""support_case_grants：客服会话 → 案件查询授权（T202，D088）

Revision ID: c9e1f5a3d7b2
Revises: b4d6f8a2c7e9
Create Date: 2026-11-07 12:00:00.000000

- 修复客服 Agent case_status_query 越权（C2）：工具仅凭 case_id 读库，
  匿名可经客服对话枚举全库案件，绕过 T171 案件令牌体系
- 门户凭案件令牌调授权端点落行；工具只查本会话已授权案件
- 唯一约束 (conversation_id, case_id)：授权幂等
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9e1f5a3d7b2"
down_revision: str | Sequence[str] | None = "b4d6f8a2c7e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "support_case_grants",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "conversation_id",
            sa.String(length=64),
            sa.ForeignKey("support_conversations.id"),
            nullable=False,
        ),
        sa.Column("case_id", sa.String(length=64), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("conversation_id", "case_id", name="uq_support_grant_conv_case"),
    )
    op.create_index(
        "ix_support_case_grants_conversation_id",
        "support_case_grants",
        ["conversation_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_support_case_grants_conversation_id", table_name="support_case_grants")
    op.drop_table("support_case_grants")
