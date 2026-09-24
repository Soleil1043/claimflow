"""案件交付队列——生产半区（T103，D044 混合方案；T153 拆分后只承载入队与交付收口）。

POST /cases 不再同步跑核赔管线：路由在与建档/材料同一事务里插入任务行
（transactional outbox——"提交成功但任务丢失"在构造上不可能），常驻单消费者
循环 CAS 认领执行；图异常退避重试，耗尽转 dead + 审计事件；interrupt 挂起是
任务的**成功终态**（outcome=interrupted + 回执快照），恢复 = 插入新的 resume 任务。

模块分工（T153）：
- 本模块：JobAction / enqueue（outbox）/ job_envelope / latest_job / deliver_case_job
- services/case_job_worker.py：消费半区（CAS 认领 / 执行 / 死信 / 派发器 / JobLoop）
- services/case_resume_guard.py：RESUME 的 checkpoint 版本门卫（T141）

砍掉的装甲（D044，相对完整 job-queue 方案）：租约/心跳/locked_by（崩溃恢复靠
启动期把 running 孤儿回收回 queued，单实例契约 replicas=1）、SKIP LOCKED 认领
（CAS 单语句在 SQLite/PG 通吃，多实例需求出现时再升级）、并发闸门（单消费者）。

口径：本模块只承载交付生命周期（queued→running→succeeded|dead），案件业务状态机
权威仍在 cases 表（D006）；回执快照（outcome/interrupt_payload）是 HTTP 响应与
轮询免读 checkpoint 的投影，不是事实。
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.logging import get_logger
from services.db.models import CaseJob
from services.db.session import get_session_factory

log = get_logger(__name__)


class JobAction(StrEnum):
    """交付动作：run 全新核赔（payload=图 input）｜ resume 恢复（payload=resume 载荷）。"""

    RUN = "run"
    RESUME = "resume"


class CaseJobConflictError(RuntimeError):
    """该案件已有活跃任务（queued/running）。路由映射 409。"""


# 重新导出消费半区公开名（T153 拆分后既有 import 路径不变；新代码建议按模块导入）
from services.case_job_worker import (  # noqa: E402,F401  (facade re-export)
    JOB_FAILED_EVENT,
    BackgroundDispatcher,
    CaseJobDispatcher,
    InlineDispatcher,
    JobLoop,
    claim_next,
    execute_job,
    make_case_dispatcher,
    requeue_orphans,
)
from services.case_resume_guard import SCHEMA_RESET_EVENT  # noqa: E402,F401


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


# ===== 交付入口：三个调用点（submit/upload/resolve）共用的一步式收口 =====


async def deliver_case_job(
    session: Any,
    *,
    case_id: str,
    action: JobAction,
    payload: dict[str, Any],
    dispatcher: CaseJobDispatcher,
    max_attempts: int | None = None,
) -> CaseJob | None:
    """受理一个交付任务：入队 → 提交整个会话 → 派发 → 回刷新后的任务行（D049）。

    三个调用点的全部生命周期差异在此一处内化（收 case_id，不收 ORM 对象）：
    - **事务边界**：commit 整个会话——submit 的建档+任务行同事务（outbox：
      受理成功而凭证丢失在构造上不可能）；upload 先行 commit 材料后再调用
      即为独立事务
    - **活跃冲突**：enqueue 撞 (case_id, seq/active) 唯一约束不仅抛错，还会
      **毒化会话**（failed 状态下后续操作全部失效）——此处自动 rollback 复位
      并返回 None；调用方按业务语义处置（upload 静默吸收恢复幂等 / resolve
      映射 409 / submit 理论不可达同样 409）
    - **派发容错**：fail-open——执行体异常已在任务内消化（重试/死信），
      此处仅兜框架级错误（如派发器本身故障），告警不阻塞调用方响应
    - **快照刷新**：返回前重读 case 与任务行（inline 档执行已改变行）

    返回 None = 存在活跃任务，本次未受理。
    """
    try:
        job = await enqueue_case_job(
            session, case_id=case_id, action=action, payload=payload,
            max_attempts=max_attempts,
        )
    except CaseJobConflictError:
        await session.rollback()
        log.info("case_job_delivery_conflict", case_id=case_id, action=str(action))
        return None
    await session.commit()

    try:
        await dispatcher.dispatch(job.id)
    except Exception as exc:  # noqa: BLE001 —— 交付触发失败：任务行仍在队（background 档会认领）
        log.warning("case_job_dispatch_kick_failed", job_id=job.id, error=str(exc)[:200])

    # 快照刷新：经 identity map 刷新调用方同会话持有的 Case 实例（原地生效），
    # 接口收 case_id——不要求调用方对象可 refresh（T108 单测逮出的接口味）
    from services.db.models import Case

    case_obj = await session.get(Case, case_id)
    if case_obj is not None:
        await session.refresh(case_obj)
    await session.refresh(job)
    return job
