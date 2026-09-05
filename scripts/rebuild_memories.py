"""申请人记忆离线重建（T100 安全网）：从 cases 事实表全量重推导终态档案。

用途：终态记忆写入失败（fail-open 只告警）后补档；Store 数据损坏后重建。
幂等：key=case_id 派生，重复执行覆盖不膨胀。

用法：uv run python -m scripts.rebuild_memories
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy import select

from services.memory.case_memory import put_case_memory, render_case_memory

# 终态状态（与 schemas.case.CaseStatus 对应）
TERMINAL_STATUSES = ("auto_issued", "closed", "referred")

# 终态原因：优先取最近一次 status_change 事件中的人可读原因
REASON_EVENT_KINDS = ("status_change",)


def _row_reason(reasons: list[str | None]) -> str | None:
    vals = [r for r in reasons if r]
    return "；".join(vals) if vals else None


async def rebuild() -> int:
    """全量重建终态案件档案，返回写入条数。"""
    from services.db.models import Case, CaseEvent
    from services.db.session import get_session_factory

    factory = get_session_factory()
    written = 0
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(Case).where(Case.status.in_(TERMINAL_STATUSES))
                )
            )
            .scalars()
            .all()
        )
        for case in rows:
            events = (
                (
                    await session.execute(
                        select(CaseEvent)
                        .where(CaseEvent.case_id == case.id)
                        .where(CaseEvent.kind.in_(REASON_EVENT_KINDS))
                        .order_by(CaseEvent.seq.asc())
                    )
                )
                .scalars()
                .all()
            )
            reasons: list[str | None] = []
            for e in events:
                payload = e.payload or {}
                if isinstance(payload, dict) and payload.get("reasons"):
                    reasons.extend(str(r) for r in payload["reasons"])
                elif isinstance(payload, dict) and payload.get("reason"):
                    reasons.append(str(payload["reason"]))
            record, embed_text = render_case_memory(
                case_id=case.id,
                user_id=case.user_id,
                case_type=case.case_type,
                outcome=case.status,
                final_decision=case.final_decision,
                approved_amount=case.approved_amount,
                reason=_row_reason(reasons),
                incident_date=case.incident_date,
            )
            await put_case_memory(record, embed_text)
            written += 1
            print(f"  ✓ {case.id} [{case.status}] {case.user_id}")
    return written


def main() -> None:
    from services.db.session import init_db

    async def _run() -> None:
        await init_db()
        started = datetime.now()
        n = await rebuild()
        print(f"重建完成：{n} 条档案（{n and f'{(datetime.now() - started).total_seconds():.1f}s'}）")

    asyncio.run(_run())


if __name__ == "__main__":
    main()
