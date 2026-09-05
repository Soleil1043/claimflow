"""案件审计与状态落库（Phase 8 T079，D006/D028 原则延续）。

CaseRecorder 协议解耦图节点与数据库：节点只依赖协议，测试注入内存实现，
运行时用 DbCaseRecorder（fail-open——审计/状态写入失败只告警，绝不阻塞核赔主流程）。

并发说明：并行 worker（policy_verify ∥ fraud_check）会在同一超步并发调用写入；
DbCaseRecorder 用实例级 asyncio.Lock 串行化——SQLite 单连接下并行事务会互相冲突
（实测丢审计事件），PostgreSQL 下串行化微小写入也无碍。
"""

from __future__ import annotations

import asyncio
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

    async def save_decision(
        self,
        case_id: str,
        *,
        title: str,
        body: str,
        conclusion: str,
        approved_amount: Any = None,
        issued_by: str = "auto",
    ) -> None:
        """保存决定书（版本化：同案 version 自增）。"""
        ...


class DbCaseRecorder:
    """DB 实现：写 case_events / cases / decision_documents 表（fail-open）。"""

    def __init__(self) -> None:
        # 实例级写锁：并行 worker 共享同一 recorder 实例，写入串行化
        self._lock = asyncio.Lock()
        # 案件内事件序号缓存（进程内同步递增；懒加载 DB 现值）
        self._seq_cache: dict[str, int] = {}

    async def event(
        self,
        case_id: str,
        kind: str,
        stage: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        try:
            async with self._lock:
                from sqlalchemy import select

                from services.db.models import CaseEvent
                from services.db.session import get_session_factory

                factory = get_session_factory()
                async with factory() as session:
                    # seq 优先取进程内缓存（同步递增，单线程事件循环下不可能重号）；
                    # 首次按案件懒加载 DB 现值。避免并行 worker 并发 SELECT max 的竞态。
                    seq = self._seq_cache.get(case_id)
                    if seq is None:
                        seq_row = await session.execute(
                            select(CaseEvent.seq)
                            .where(CaseEvent.case_id == case_id)
                            .order_by(CaseEvent.seq.desc())
                            .limit(1)
                        )
                        seq = seq_row.scalar_one_or_none() or 0
                    seq += 1
                    self._seq_cache[case_id] = seq
                    session.add(
                        CaseEvent(
                            case_id=case_id, kind=kind, stage=stage, seq=seq, payload=payload
                        )
                    )
                    await session.commit()
        except Exception as exc:  # noqa: BLE001 —— fail-open：审计失败不阻塞核赔
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
            async with self._lock:
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
                    await session.execute(
                        update(Case).where(Case.id == case_id).values(**values)
                    )
                    await session.commit()
        except Exception as exc:  # noqa: BLE001 —— fail-open
            log.warning("case_update_failed", case_id=case_id, error=str(exc))

    async def save_decision(
        self,
        case_id: str,
        *,
        title: str,
        body: str,
        conclusion: str,
        approved_amount: Any = None,
        issued_by: str = "auto",
    ) -> None:
        try:
            async with self._lock:
                from sqlalchemy import func as sql_func
                from sqlalchemy import select

                from services.db.models import DecisionDocument
                from services.db.session import get_session_factory

                factory = get_session_factory()
                async with factory() as session:
                    max_version = (
                        await session.execute(
                            select(sql_func.max(DecisionDocument.version)).where(
                                DecisionDocument.case_id == case_id
                            )
                        )
                    ).scalar_one_or_none()
                    session.add(
                        DecisionDocument(
                            case_id=case_id,
                            version=(max_version or 0) + 1,
                            title=title,
                            body=body,
                            conclusion=conclusion,
                            approved_amount=approved_amount,
                            issued_by=issued_by,
                        )
                    )
                    await session.commit()
        except Exception as exc:  # noqa: BLE001 —— fail-open
            log.warning("decision_save_failed", case_id=case_id, error=str(exc))


def get_default_recorder() -> CaseRecorder:
    """运行时默认记录器（DB）。"""
    return DbCaseRecorder()
