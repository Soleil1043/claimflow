"""证件号列加密扩容（T176，D081 第 2 层 / D082）

Revision ID: b4d6f8a2c7e9
Revises: a2c8e4f1b6d3
Create Date: 2026-10-10 12:00:00.000000

- policies.holder_id_card / medical_records.patient_id_card：String(18) → String(128)
  （密文 v1:<base64(iv+ct)> 约 68 字符，18 位身份证密文实测 68）
- 数据迁移：存量明文行回填密文（幂等：v1: 前缀跳过；decrypt 对非前缀值
  原样返回，滚动迁移期两态共存）
- 密钥：运行时 PII_ENCRYPTION_KEY（与 app 同源；迁移须在与应用相同的密钥
  环境下执行——密钥轮换属后续 key-version 议题，不在本迁移范围）
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4d6f8a2c7e9"
down_revision: str | Sequence[str] | None = "a2c8e4f1b6d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (表, 列) 清单：本次纳入加密的证件号列
_COLUMNS: tuple[tuple[str, str], ...] = (
    ("policies", "holder_id_card"),
    ("medical_records", "patient_id_card"),
)


def upgrade() -> None:
    """列扩容 + 存量明文回填密文。"""
    for table, column in _COLUMNS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column, type_=sa.String(128), existing_type=sa.String(18)
            )

    from app.core.crypto import encrypt_pii

    conn = op.get_bind()
    for table, column in _COLUMNS:
        rows = conn.execute(sa.text(f"SELECT id, {column} FROM {table}")).fetchall()  # noqa: S608
        reencrypted = 0
        for pk, value in rows:
            if not value or str(value).startswith("v1:"):
                continue
            conn.execute(
                sa.text(f"UPDATE {table} SET {column} = :v WHERE id = :i"),  # noqa: S608
                {"v": encrypt_pii(str(value)), "i": pk},
            )
            reencrypted += 1
        if reencrypted:
            print(f"  {table}.{column}: {reencrypted} 行明文已回填密文")


def downgrade() -> None:
    """密文行解密回明文 + 列还原（密钥须与应用一致）。"""
    from app.core.crypto import decrypt_pii

    conn = op.get_bind()
    for table, column in _COLUMNS:
        rows = conn.execute(sa.text(f"SELECT id, {column} FROM {table}")).fetchall()  # noqa: S608
        for pk, value in rows:
            if not value or not str(value).startswith("v1:"):
                continue
            conn.execute(
                sa.text(f"UPDATE {table} SET {column} = :v WHERE id = :i"),  # noqa: S608
                {"v": decrypt_pii(str(value)), "i": pk},
            )
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column, type_=sa.String(18), existing_type=sa.String(128)
            )
