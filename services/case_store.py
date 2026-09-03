"""案件审计与状态落库（Phase 8 T079，D006/D028 原则延续）。

CaseRecorder 协议解耦图节点与数据库：节点只依赖协议，测试注入内存实现，
运行时用 DbCaseRecorder（fail-open——审计/状态写入失败只告警，绝不阻塞核赔主流程）。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.core.logging import get_logger

log = get_logger(__name__)


@runtime_checkable
class CaseRecorder(Protocol):
    """案件事件与状态写入协议（异步接口）。"""

    async def event(
        self,
        case_id: str,
        kind: str,
        stage: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """追加一条案件事件（append-only）。"""
        ...

    async def update_case(
        self,
        case_id: str,
        *,
        status: str | None = None,
        case_type: str | None = None,
        final_decision: str | None = None,
        approved_amount: Any = None,
    ) -> None:
        """更新案件主档字段（None=不更新）。"""
        ...


class DbCaseRecorder:
    """DB 实现：写 case_events / cases 表（fail-open）。"""

    async def event(
        self,
        case_id: str,
        kind: str,
        stage: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        try:
            from sqlalchemy import select

            from services.db.models import CaseEvent
            from services.db.session import get_session_factory

            factory = get_session_factory()
            async with factory() as session:
                seq_row = await session.execute(
                    select(CaseEvent.seq)
                    .where(CaseEvent.case_id == case_id)
                    .order_by(CaseEvent.seq.desc())
                    .limit(1)
                )
                seq = (seq_row.scalar_one_or_none() or 0) + 1
                session.add(
                    CaseEvent(
                        case_id=case_id, kind=kind, stage=stage, seq=seq, payload=payload
                    )
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001——fail-open：审计失败不阻塞核赔
            log.warning("case_event_write_failed", case_id=case_id, kind=kind, error=str(exc))

    async def update_case(
        self,
        case_id: str,
        *,
        status: str | None = None,
        case_type: str | None = None,
        final_decision: str | None = None,
        approved_amount: Any = None,
    ) -> None:
        try:
            import datetime as dt

            from sqlalchemy import update

            from services.db.models import Case
            from services.db.session import get_session_factory

            values: dict[str, Any] = {"updated_at": dt.datetime.now()}
            if status is not None:
                values["status"] = status
            if case_type is not None:
                values["case_type"] = case_type
            if final_decision is not None:
                values["final_decision"] = final_decision
            if approved_amount is not None:
                values["approved_amount"] = approved_amount
            factory = get_session_factory()
            async with factory() as session:
                await session.execute(update(Case).where(Case.id == case_id).values(**values))
                await session.commit()
        except Exception as exc:  # noqa: BLE001——fail-open
            log.warning("case_update_failed", case_id=case_id, error=str(exc))


def get_default_recorder() -> CaseRecorder:
    """运行时默认记录器（DB）。"""
    return DbCaseRecorder()
