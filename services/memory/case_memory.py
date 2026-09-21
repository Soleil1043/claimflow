"""申请人记忆（T100）：核赔案件终态档案——④ 语义记忆层与 ③ 事实层的对接。

领域口径（D042）：
- 记忆条目 = 案件终态的结构化档案（结论/核定金额/原因/日期），**确定性渲染零 LLM**
  （案件事实本就是结构化数据，无需 LLM 摘要）
- key = case_id 派生，幂等 upsert；条目是 cases 表事实的派生视图，可离线重建
  （scripts/rebuild_memories.py 安全网）
- 只写终态（auto_issued / closed / 终态 referred）——过程流转归 case_events 审计层，
  检索窗口不被中间态噪音污染
- 与风控的边界：claim_records 是结构化统计信号（评分公式消费），本记忆是语义档案
  （人或 LLM 消费）

写路径 fail-open（memory_enabled 开关；异常只告警），读路径异常返回空列表。
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.core.config import settings
from app.core.logging import get_logger
from services.memory.long_term import search_store_items

log = get_logger(__name__)

# Store 命名空间：user 维度，value.kind 区分条目种类（case = 申请人核赔档案）
_MEMORY_USER_NAMESPACE = "memory"
CASE_KIND = "case"

MAX_APPLICANT_HISTORY = 5


class CaseMemoryRecord(BaseModel):
    """一条申请人核赔档案（写入 Store 的业务结构，确定性渲染）。"""

    kind: str = CASE_KIND
    case_id: str
    user_id: str
    case_type: str
    outcome: str  # auto_issued | closed | referred
    final_decision: str | None = None
    approved_amount: str | None = None
    reason: str | None = None
    incident_date: str = ""
    updated_at: str = ""
    # 写入时终态链路置信度：auto 签发 = min(材料, 责任)；human 三路径（坐席签批/
    # escape/REJECT 保守兜底）= 1.0 确定性事实。坐席档案卡展示（T138，D058）
    confidence: float = 1.0
    # 应用层 TTL（ISO 时间戳；空=永不过期）——InMemoryStore 不支持原生 ttl（实测
    # aput(ttl=) 抛 NotImplementedError），故值内携带、读取惰性过滤清理（D058）
    expires_at: str | None = None


def case_memory_key(case_id: str) -> str:
    """确定性 key：一案件一条档案，upsert 覆盖实现幂等（重跑/重建安全）。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"claimflow:case_memory:{case_id}"))


def render_case_memory(
    *,
    case_id: str,
    user_id: str,
    case_type: str,
    outcome: str,
    final_decision: str | None = None,
    approved_amount: Decimal | str | None = None,
    reason: str | None = None,
    incident_date: Any = None,
    confidence: float = 1.0,
) -> tuple[CaseMemoryRecord, str]:
    """案件终态 → (记忆条目, 嵌入文本)。纯函数，零 LLM、零 I/O。

    reason 来自调用方（各终态分支最清楚自己的原因口径）；
    不含 PII 字段——档案只承载核赔结论事实。
    """
    amount = (
        str(Decimal(str(approved_amount)).quantize(Decimal("0.01")))
        if approved_amount is not None
        else None
    )
    expires_at = (
        (dt.datetime.now() + dt.timedelta(days=settings.memory_ttl_days)).isoformat(
            timespec="seconds"
        )
        if settings.memory_ttl_days > 0
        else None
    )
    record = CaseMemoryRecord(
        case_id=case_id,
        user_id=user_id,
        case_type=case_type,
        outcome=outcome,
        final_decision=final_decision,
        approved_amount=amount,
        reason=(reason or None),
        incident_date=str(incident_date or ""),
        updated_at=dt.datetime.now().isoformat(timespec="seconds"),
        confidence=round(float(confidence), 2),
        expires_at=expires_at,
    )
    return record, _build_embed_text(record)


def _build_embed_text(record: CaseMemoryRecord) -> str:
    parts = [f"申请人历史核赔档案：案件 {record.case_id}（{record.case_type}）"]
    if record.final_decision:
        parts.append(f"结论 {record.final_decision}")
    if record.approved_amount:
        parts.append(f"核定金额 {record.approved_amount} 元")
    if record.reason:
        parts.append(f"原因：{record.reason}")
    if record.incident_date:
        parts.append(f"出险日期 {record.incident_date}")
    return "，".join(parts)


async def put_case_memory(record: CaseMemoryRecord, embed_text: str) -> None:
    """Store upsert（幂等；异步接口优先，BUG-003 口径）。异常向上抛，由调用方定旁路语义。"""
    from services.memory.long_term import _ensure_pg_setup, get_memory_store

    store = get_memory_store()
    await _ensure_pg_setup(store)
    value = {**record.model_dump(), "embed_text": embed_text}
    aput = getattr(store, "aput", None)
    if aput is not None:
        await aput((_MEMORY_USER_NAMESPACE, record.user_id), case_memory_key(record.case_id), value)
    else:
        result = store.put(
            (_MEMORY_USER_NAMESPACE, record.user_id), case_memory_key(record.case_id), value
        )
        import inspect

        if inspect.isawaitable(result):
            await result


async def write_case_memory(
    state: dict[str, Any],
    *,
    outcome: str,
    reason: str | None = None,
    final_decision: str | None = None,
    approved_amount: Decimal | str | None = None,
    confidence: float = 1.0,
) -> None:
    """终态钩子（fail-open——记忆是旁路路径，失败只告警）。

    终态事实优先取显式参数（终态分支最清楚自己的值），缺省回落 state；
    四条终态路径（auto 签发、坐席签发、escape 转人工、REJECT 安全兜底）各调用一次。
    注意 auto_adjudicate 的转人工分支不是终态（案件继续走 human_gate 签批）。

    置信度门控（T138，D058）：confidence < memory_confidence_floor 时不写入
    （低置信 not_covered 档案从源头拦断）；human 路径不传即 1.0（确定性事实）。
    """
    if not settings.memory_enabled:
        return
    if float(confidence) < settings.memory_confidence_floor:
        log.warning(
            "case_memory_skipped_low_confidence",
            case_id=state.get("case_id"),
            user_id=state.get("user_id"),
            confidence=round(float(confidence), 2),
            floor=settings.memory_confidence_floor,
        )
        return
    try:
        record, embed_text = render_case_memory(
            case_id=str(state.get("case_id") or ""),
            user_id=str(state.get("user_id") or ""),
            case_type=str(state.get("case_type") or "unknown"),
            outcome=outcome,
            final_decision=final_decision
            or (str(state.get("final_decision") or "") or None),
            approved_amount=approved_amount
            if approved_amount is not None
            else state.get("approved_amount"),
            reason=reason,
            incident_date=state.get("incident_date"),
            confidence=confidence,
        )
        if not record.user_id:
            return
        await put_case_memory(record, embed_text)
        log.info(
            "case_memory_written",
            case_id=record.case_id,
            user_id=record.user_id,
            outcome=record.outcome,
            confidence=record.confidence,
        )
    except Exception as exc:  # noqa: BLE001 fail-open：档案写入失败不阻塞核赔
        log.warning("case_memory_write_failed", case_id=state.get("case_id"), error=str(exc)[:200])


async def search_case_memories(
    user_id: str, *, exclude_case_id: str | None = None, top_k: int | None = None
) -> list[CaseMemoryRecord]:
    """检索申请人核赔档案（kind=case 过滤 + TTL 惰性过期；禁用/异常 → 空列表，永不抛错）。"""
    if not settings.memory_enabled or not user_id:
        return []
    try:
        items = await search_store_items(
            user_id, query="申请人历史核赔案件 结论 金额 拒赔 签发", limit=(top_k or MAX_APPLICANT_HISTORY) + 4
        )
        now = dt.datetime.now()
        expired_case_ids: list[str] = []
        records: list[CaseMemoryRecord] = []
        for item in items:
            value = item.value or {}
            if value.get("kind") != CASE_KIND:
                continue  # 会话记忆等其他种类不进档案视图
            if exclude_case_id and value.get("case_id") == exclude_case_id:
                continue
            expires_at = value.get("expires_at")
            if expires_at:
                try:
                    if dt.datetime.fromisoformat(str(expires_at)) <= now:
                        expired_case_ids.append(str(value.get("case_id") or ""))
                        continue
                except ValueError:
                    pass  # 损坏时间戳按不过期处理
            records.append(CaseMemoryRecord.model_validate(value))
        # TTL 惰性清理：顺手删除已过期条目（单个失败不影响读路径）
        for cid in expired_case_ids:
            if not cid:
                continue
            try:
                await _adelete_case_memory(user_id, cid)
                log.info("case_memory_expired_deleted", user_id=user_id, case_id=cid)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "case_memory_expire_cleanup_failed",
                    user_id=user_id, case_id=cid, error=str(exc)[:120],
                )
        records.sort(key=lambda r: r.updated_at, reverse=True)
        return records[: top_k or MAX_APPLICANT_HISTORY]
    except Exception as exc:  # noqa: BLE001 读路径旁路
        log.warning("case_memory_search_failed", user_id=user_id, error=str(exc)[:200])
        return []


# ===== 删除链路（T138，D055-3） =====


async def _adelete_case_memory(user_id: str, case_id: str) -> None:
    """Store 条目删除（异步接口优先，BUG-003 口径）。异常向上抛。"""
    import inspect

    from services.memory.long_term import _ensure_pg_setup, get_memory_store

    store = get_memory_store()
    await _ensure_pg_setup(store)
    adelete = getattr(store, "adelete", None)
    if adelete is not None:
        await adelete((_MEMORY_USER_NAMESPACE, user_id), case_memory_key(case_id))
        return
    result = store.delete(
        (_MEMORY_USER_NAMESPACE, user_id), case_memory_key(case_id)
    )
    if inspect.isawaitable(result):
        await result


async def delete_case_memory(user_id: str, case_id: str) -> bool:
    """删除一条申请人核赔档案（坐席操作）。False=条目不存在或记忆禁用。

    语义（D058）：删除即从档案视图消失；scripts/rebuild_memories.py 是显式人工
    安全网，重跑会按 cases 表终态重建——非 tombstone 永久删除。
    """
    import inspect

    if not settings.memory_enabled or not user_id or not case_id:
        return False
    try:
        from services.memory.long_term import _ensure_pg_setup, get_memory_store

        store = get_memory_store()
        await _ensure_pg_setup(store)
        key = case_memory_key(case_id)
        aget = getattr(store, "aget", None)
        item = (
            await aget((_MEMORY_USER_NAMESPACE, user_id), key)
            if aget is not None
            else store.get((_MEMORY_USER_NAMESPACE, user_id), key)
        )
        if inspect.isawaitable(item):
            item = await item
        if item is None:
            return False
        await _adelete_case_memory(user_id, case_id)
        log.info("case_memory_deleted", user_id=user_id, case_id=case_id)
        return True
    except Exception as exc:  # noqa: BLE001 删除失败向上抛由 API 层翻译
        log.warning("case_memory_delete_failed", user_id=user_id, case_id=case_id, error=str(exc)[:200])
        raise


def format_case_memories(records: list[CaseMemoryRecord], max_chars: int = 800) -> str:
    """档案列表 → 展示/注入文本（坐席详情与路由快照共用口径）。"""
    lines = []
    for r in records:
        parts = [f"{r.updated_at[:10] or '—'} {r.case_id}（{r.case_type}）"]
        if r.final_decision:
            parts.append(f"结论 {r.final_decision}")
        if r.approved_amount:
            parts.append(f"核定 {r.approved_amount} 元")
        if r.reason:
            parts.append(f"原因 {r.reason}")
        lines.append("；".join(parts))
    return "\n".join(lines)[:max_chars]
