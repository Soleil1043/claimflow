"""drop v1 会话域死表（conversations / messages / human_tickets，T104）

Revision ID: d9a5c1e8f3b7
Revises: c8e4f2a6b1d9
Create Date: 2026-09-06 16:00:00.000000

v1 咨询产品代码于 T093 删除，T104 清理遗留：三张表自那时起零读写
（与 D040 保留 EvalRunRecord 不同——那是评测基建有复用语义，这三张是
v1 产品本体，永无复用）。表内仅 PoC mock 数据。ORM 模型同任务移除。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9a5c1e8f3b7"
down_revision: str | Sequence[str] | None = "c8e4f2a6b1d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 依赖序：messages 外键引用 conversations，先删子表
_DEAD_TABLES = ("messages", "conversations", "human_tickets")


def upgrade() -> None:
    """Upgrade schema."""
    for table in _DEAD_TABLES:
        op.drop_table(table)


def downgrade() -> None:
    """Downgrade schema：v1 死表不重建（downgrade 仅保证迁移链可回走，
    表结构定义已随 ORM 模型删除——需要时从 git 历史恢复）。"""
    pass
