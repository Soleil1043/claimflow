"""客服会话域两表（T132，D057）

Revision ID: b7d2e6f9a3c1
Revises: d9a5c1e8f3b7
Create Date: 2026-09-22 10:00:00.000000

support_conversations（会话状态机权威：ai → escalated → closed）+
support_messages（append-only 消息投影：UI 历史与坐席 transcript 单源，
Agent 多轮记忆由本表 replay，D057-2）。与核赔案件无关，无外键关联 cases。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d2e6f9a3c1"
down_revision: str | Sequence[str] | None = "d9a5c1e8f3b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_column() -> sa.Column:
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "support_conversations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ai"),
        sa.Column("escalated_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("escalated_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_support_conversations_status", "support_conversations", ["status"]
    )
    op.create_table(
        "support_messages",
        sa.Column("id", _id_column(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["support_conversations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_support_messages_conversation_id", "support_messages", ["conversation_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_support_messages_conversation_id", table_name="support_messages"
    )
    op.drop_table("support_messages")
    op.drop_index(
        "ix_support_conversations_status", table_name="support_conversations"
    )
    op.drop_table("support_conversations")
