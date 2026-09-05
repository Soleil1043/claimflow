"""case_events (case_id, seq) 唯一约束（T095 seq 单一分配器兜底）

Revision ID: b5f9c3d7e2a4
Revises: d7f4b8c2a9e1
Create Date: 2026-09-05 12:00:00.000000

背景（评审候选 4 / D040 补记）：seq 历史上存在两个分配器（图内 recorder 进程内缓存
与 API 路由 SELECT max+1），互不知情会在材料上传后产生重号。T095 归一为共享
recorder 单例；本迁移为 DB 层兜底：先清理历史重号行（按 (case_id, seq) 保留最小 id），
再加唯一约束 uq_case_event_case_seq。
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "b5f9c3d7e2a4"
down_revision: str | Sequence[str] | None = "d7f4b8c2a9e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEDUP_SQL = """
DELETE FROM case_events
WHERE id NOT IN (
    SELECT MIN(id) FROM case_events GROUP BY case_id, seq
)
"""


def upgrade() -> None:
    """Upgrade schema."""
    # 历史重号行清理（保留每组最小 id = 最早写入），否则唯一约束创建失败
    op.execute(text(DEDUP_SQL))
    op.create_unique_constraint(
        "uq_case_event_case_seq", "case_events", ["case_id", "seq"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_case_event_case_seq", "case_events", type_="unique")
