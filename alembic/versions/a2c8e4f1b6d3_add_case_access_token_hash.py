"""cases 加 access_token_hash：门户访问令牌哈希（T171，D077）

Revision ID: a2c8e4f1b6d3
Revises: f3a7c2d9e4b1
Create Date: 2026-10-09 12:00:00.000000

- 案件门户越权读取修复（IDOR）：POST /cases 创建回执一次性下发明文令牌，
  库内只存 sha256 哈希；GET /cases/{id} 与材料上传凭 X-Case-Token 或 X-Staff-Key 访问
- 存量行 NULL = 无门户令牌（迁移前案件），仅坐席通道可读——不回填
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2c8e4f1b6d3"
down_revision: str | Sequence[str] | None = "f3a7c2d9e4b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("cases") as batch:
        batch.add_column(sa.Column("access_token_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("cases") as batch:
        batch.drop_column("access_token_hash")
