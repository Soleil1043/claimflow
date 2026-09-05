"""案件交付队列（T103，D044 混合方案：任务表凭证 + 常驻单消费者，无租约装甲）。

POST /cases 不再同步跑核赔管线：路由在与建档/材料同一事务里插入任务行
（transactional outbox——"提交成功但任务丢失"在构造上不可能），常驻单消费者
循环 CAS 认领执行；图异常退避重试，耗尽转 dead + 审计事件；interrupt 挂起是
任务的**成功终态**（outcome=interrupted + 回执快照），恢复 = 插入新的 resume 任务。

砍掉的装甲（D044，相对完整 job-queue 方案）：租约/心跳/locked_by（崩溃恢复靠
启动期把 running 孤儿回收回 queued，单实例契约 replicas=1）、SKIP LOCKED 认领
（CAS 单语句在 SQLite/PG 通吃，多实例需求出现时再升级）、并发闸门（单消费者）。

口径：本模块只承载交付生命周期（queued→running→succeeded|dead），案件业务状态机
权威仍在 cases 表（D006）；回执快照（outcome/interrupt_payload）是 HTTP 响应与
轮询免读 checkpoint 的投影，不是事实。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from langgraph.types import Command
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.logging import get_logger
from services.case_store import CaseRecorder
from services.db.models import CaseJob
from services.db.session import get_session_factory
from services.observability import metrics
from services.observability.token_tracker import track_case

log = get_logger(__name__)

# 重试耗尽死信的审计事件 kind（坐席/运维可见的失败凭证）
JOB_FAILED_EVENT = "job_failed"


class JobAction(StrEnum):
    """交付动作：run 全新核赔（payload=图 input）｜ resume 恢复（payload=resume 载荷）。"""

    RUN = "run"
    RESUME = "resume"


class CaseJobConflictError(RuntimeError):
    """该案件已有活跃任务（queued/running）。路由映射 409。"""


# ===== 生产半区：入队（outbox 语义） =====


def _json_safe(value: Any) -> Any:
    """payload 深度 JSON 安全化（队列边界职责，D044 预判项）。

    Decimal → str（金额全链路字符串口径）、date/datetime → ISO 串；
    图节点对这两类已有显式收敛（amount_calc Decimal(str(...))、
    policy_verify fromisoformat——T079 坑位记录）。
    """
    from decimal import Decimal as _Decimal

    if isinstance(value, _Decimal):
        return str(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


async def enqueue_case_job(
    session: Any,
    *,
    case_id: str,
    action: JobAction | str,
    payload: dict[str, Any],
    max_attempts: int | None = None,
) -> CaseJob:
    """在同一事务内插入任务行。**不提交**——commit 权归调用方（outbox 核心）。

    await flush 立即撞活跃唯一约束（partial unique index）→ 翻译为
    CaseJobConflictError，冲突时调用方应 rollback（未提交的建档/材料一并回滚）
    或按业务语义处置。
    """
    job = CaseJob(
        case_id=case_id,
        action=action.value if isinstance(action, JobAction) else str(action),
        payload=_json_safe(payload),
        max_attempts=max_attempts or settings.case_jobs_max_attempts,
        run_after=dt.datetime.now(),
    )
    session.add(job)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise CaseJobConflictError(f"案件 {case_id} 已有活跃交付任务") from exc
    return job


def job_envelope(job: CaseJob) -> dict[str, Any]:
    """任务行 → API 响应投影（schemas.api.CaseJobOut 口径）。"""
    return {
        "job_id": job.id,
        "action": job.action,
        "status": job.status,
        "outcome": job.outcome,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "error": job.last_error,
    }


async def latest_job(case_id: str) -> CaseJob | None:
    """案件最近一次交付任务（详情/轮询的 job 字段）。"""
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(CaseJob)
                .where(CaseJob.case_id == case_id)
                .order_by(CaseJob.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


# ===== 消费半区：CAS 认领 + 执行 =====


async def claim_next() -> CaseJob | None:
    """认领下一个到期任务（单语句 CAS：SELECT 到期最早 → 条件 UPDATE 抢占）。

    rowcount=0 即被抢走（或状态已变），返回 None 由调用方重试/空转。
    SQLite 与 PG 通吃（无 SKIP LOCKED 依赖，D044 单实例契约下排他性等价）。
    """
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(CaseJob.id)
                .where(CaseJob.status == "queued", CaseJob.run_after <= dt.datetime.now())
                .order_by(CaseJob.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        result = await session.execute(
            update(CaseJob)
            .where(CaseJob.id == row, CaseJob.status == "queued")
            .values(
                status="running",
                attempt=CaseJob.attempt + 1,
                started_at=dt.datetime.now(),
            )
        )
        await session.commit()
        if result.rowcount == 0:
            return None  # 抢输（并发认领窗口）
        return await session.get(CaseJob, row)


async def execute_job(job: CaseJob, *, graph: Any, recorder: CaseRecorder) -> None:
    """执行一个已认领任务至终态（succeeded / queued 重试 / dead）。

    行级状态更新走独立会话（与图内 recorder 会话互不干扰）；
    观测（CASE_DURATION/CASE_TOKENS）随执行体——路由不再感知。
    """
    started = time.monotonic()
    try:
        with track_case(job.case_id):
            invocation: Any = (
                Command(resume=job.payload)
                if job.action == JobAction.RESUME.value
                else job.payload
            )
            result = await graph.ainvoke(
                invocation,
                config={
                    "configurable": {"thread_id": job.case_id},
                    "recursion_limit": 60,
                },
            )
        if job.action == JobAction.RUN.value:
            metrics.record_case_duration(time.monotonic() - started)

        interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
        if interrupts:
            value = getattr(interrupts[0], "value", None)
            payload = value if isinstance(value, dict) else {}
            await _finish(
                job.id,
                status="succeeded",
                outcome="interrupted",
                interrupt_payload=payload,
            )
        else:
            await _finish(job.id, status="succeeded", outcome="completed")
    except Exception as exc:  # noqa: BLE001 —— 交付级异常：重试或死信，不向上抛
        if job.attempt >= job.max_attempts:
            await _finish(job.id, status="dead", outcome=None, error=str(exc)[:500])
            try:
                await recorder.event(
                    job.case_id,
                    JOB_FAILED_EVENT,
                    payload={"attempts": job.attempt, "error": str(exc)[:300]},
                )
            except Exception:  # noqa: BLE001 —— 审计失败不掩盖死信状态
                log.warning("job_failed_audit_missed", job_id=job.id)
            log.warning("case_job_dead", job_id=job.id, case_id=job.case_id,
                        attempts=job.attempt, error=str(exc)[:200])
        else:
            delay = settings.case_jobs_backoff_base_s * (2 ** (job.attempt - 1))
            await _retry(job.id, error=str(exc)[:500], delay_s=delay)
            log.warning("case_job_retry_scheduled", job_id=job.id,
                        attempt=job.attempt, delay_s=delay, error=str(exc)[:200])


async def _finish(
    job_id: int,
    *,
    status: str,
    outcome: str | None,
    interrupt_payload: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            update(CaseJob)
            .where(CaseJob.id == job_id)
            .values(
                status=status,
                outcome=outcome,
                interrupt_payload=interrupt_payload,
                last_error=error,
                finished_at=dt.datetime.now(),
            )
        )
        await session.commit()


async def _retry(job_id: int, *, error: str, delay_s: float) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            update(CaseJob)
            .where(CaseJob.id == job_id)
            .values(
                status="queued",
                last_error=error,
                run_after=dt.datetime.now() + dt.timedelta(seconds=delay_s),
            )
        )
        await session.commit()


async def requeue_orphans() -> int:
    """启动期回收：崩溃遗留的 running 孤儿全部回 queued（单实例契约）。

    返回回收行数。执行中途被杀的任务由重跑幂等承载（checkpoint 从上个
    superstep 续跑；重放 superstep 的审计重复为已知噪声，D044 接受）。
    """
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            update(CaseJob)
            .where(CaseJob.status == "running")
            .values(status="queued", run_after=dt.datetime.now())
        )
        await session.commit()
        if result.rowcount:
            log.info("case_jobs_orphans_requeued", count=result.rowcount)
        return result.rowcount or 0


# ===== 派发器（seam：执行时机的两个真 adapter） =====


@runtime_checkable
class CaseJobDispatcher(Protocol):
    """请求路径的交付触发点。"""

    async def dispatch(self, job_id: int) -> None: ...


class InlineDispatcher:
    """同步执行 adapter（测试/兼容档）：dispatch 即执行到终态，响应保留同步语义。"""

    def __init__(self, graph: Any, recorder: CaseRecorder):
        self._graph = graph
        self._recorder = recorder

    async def dispatch(self, job_id: int) -> None:
        factory = get_session_factory()
        async with factory() as session:
            job = await session.get(CaseJob, job_id)
        if job is None:
            msg = f"任务行 {job_id} 不存在（enqueue 未提交？）"
            raise RuntimeError(msg)
        await execute_job(job, graph=self._graph, recorder=self._recorder)


class BackgroundDispatcher:
    """后台循环 adapter（生产默认）：no-op——常驻消费者 ≤ poll_interval 内认领。"""

    async def dispatch(self, job_id: int) -> None:
        return None


def make_case_dispatcher(graph: Any, recorder: CaseRecorder) -> CaseJobDispatcher:
    """按 settings.case_jobs_execution 装配派发器（inline | background）。"""
    if settings.case_jobs_execution == "inline":
        return InlineDispatcher(graph, recorder)
    return BackgroundDispatcher()


class JobLoop:
    """常驻单消费者循环（background 档，lifespan 持有）。

    start()：先回收孤儿再拉起循环任务；stop()：停止认领 → 排水等待在飞任务
    （超时放弃，行留 running 由下次启动回收）。tick() 独立暴露供确定性测试。
    """

    def __init__(
        self,
        graph: Any,
        recorder: CaseRecorder,
        *,
        poll_interval_s: float | None = None,
    ):
        self._graph = graph
        self._recorder = recorder
        self._poll = poll_interval_s or settings.case_jobs_poll_interval_s
        self._task: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        await requeue_orphans()
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="case-job-loop")
        log.info("case_job_loop_started", poll_interval_s=self._poll)

    async def tick(self) -> CaseJob | None:
        """处理至多一个到期任务（返回 None=队空）。"""
        job = await claim_next()
        if job is None:
            return None
        await execute_job(job, graph=self._graph, recorder=self._recorder)
        return job

    async def _run(self) -> None:
        while not self._stopping:
            try:
                job = await self.tick()
            except Exception as exc:  # noqa: BLE001 —— 循环永不因单任务逻辑退出
                log.warning("case_job_loop_tick_error", error=str(exc)[:200])
                job = None
            if job is None:
                await asyncio.sleep(self._poll)

    async def stop(self, timeout_s: float | None = None) -> None:
        """停止认领并排水。超时放弃在飞任务（行留 running，下次启动回收）。"""
        self._stopping = True
        if self._task is None:
            return
        timeout = timeout_s or settings.case_jobs_drain_timeout_s
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout)
        except (TimeoutError, asyncio.CancelledError):
            self._task.cancel()
            log.warning("case_job_loop_drain_timeout", timeout_s=timeout)
        log.info("case_job_loop_stopped")
