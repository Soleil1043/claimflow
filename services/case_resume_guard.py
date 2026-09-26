"""RESUME 恢复门卫（T141，D061 checkpoint schema 版本策略；T153 自 case_jobs 拆出）。

checkpoint 的 schema_version 匹配 → Command(resume=载荷)；
不匹配（含旧 checkpoint 无此字段）→ 降级全新重跑：删旧 thread checkpoint，
取该案最近一次 RUN 任务的原始图输入重新执行（执行进度丢弃；案件事实权威在
cases 表 D006，结果无损），schema_reset 审计事件留痕。
"""

from __future__ import annotations

from typing import Any

from langgraph.types import Command
from sqlalchemy import select

from app.core.logging import get_logger
from services.case_job_worker import JobAction
from services.case_store import CaseRecorder
from services.db.models import CaseJob
from services.db.session import get_session_factory
from state import CASE_SCHEMA_VERSION

log = get_logger(__name__)

# checkpoint schema 版本不匹配、resume 降级全新重跑的审计事件 kind（T141，D061）
SCHEMA_RESET_EVENT = "schema_reset"


async def resume_invocation(
    job: CaseJob, *, graph: Any, recorder: CaseRecorder
) -> Any:
    """RESUME 载荷构造：版本匹配走 Command(resume)，不匹配降级全新重跑。"""
    config = {"configurable": {"thread_id": job.case_id}}
    version: int | None = None
    try:
        snapshot = await graph.aget_state(config)
        values = getattr(snapshot, "values", None) if snapshot is not None else None
        version = (values or {}).get("schema_version")
    except Exception:  # noqa: BLE001 —— aget_state 不可用/失败：保守按不匹配降级
        version = None

    if version == CASE_SCHEMA_VERSION:
        return Command(resume=job.payload)

    log.warning(
        "case_resume_schema_mismatch",
        case_id=job.case_id,
        expected=CASE_SCHEMA_VERSION,
        found=version,
    )
    # 旧 thread 状态必须清掉——否则全新输入在既有 channel 上合并，旧阶段结论污染重跑
    checkpointer = getattr(graph, "checkpointer", None)
    delete_thread = getattr(checkpointer, "adelete_thread", None)
    if delete_thread is not None:
        await delete_thread(job.case_id)

    factory = get_session_factory()
    async with factory() as session:
        run_job = (
            await session.execute(
                select(CaseJob)
                .where(
                    CaseJob.case_id == job.case_id,
                    CaseJob.action == JobAction.RUN.value,
                )
                .order_by(CaseJob.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if run_job is None:
        # 理论不可达（RESUME 必有前置 RUN）；显式失败进入重试/死信，不盲 resume 旧格式
        msg = f"案件 {job.case_id} 的 resume 降级失败：找不到原始 RUN 图输入"
        raise RuntimeError(msg)

    await recorder.event(
        job.case_id,
        SCHEMA_RESET_EVENT,
        payload={
            "expected": CASE_SCHEMA_VERSION,
            "found": version,
            "action": "fresh_rerun",
        },
    )
    return run_job.payload
