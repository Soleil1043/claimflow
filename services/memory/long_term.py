"""长期记忆服务（T034 写路径 + T035 读注入；T048 迁移 LangGraph 官方 Store）。

存储层（T048，D021/ADR-007）：LangGraph Store 体系——
- dev：InMemoryStore（进程内，零依赖）
- prod：AsyncPostgresStore（langgraph.store.postgres，复用 psycopg 连接）
- 向量检索：Store 内建 index（IndexConfig：dims=1024 / embed=BGE-M3 / fields=[embed_text]），
  namespace 按 (user_id,) 隔离，key 为会话确定性 id（upsert 幂等覆盖）
- 自研 Qdrant long_term_memory collection 与注入管线删除；Qdrant 仅保留 RAG 用途

写路径（T034）：会话累计 N 轮（用户消息数）时生成对话摘要 + 关键实体
（保单号/诊断/金额），嵌入文本 = 摘要 + 实体字段（实体入向量，保证
"我上次问的那张保单"类实体查询可命中）。

读路径（T035）：新会话首轮按 user_id 命名空间语义检索 top-k 历史摘要（相似度低于
memory_min_score 的噪声过滤），拼装注入 system prompt——跨会话上下文连贯；
无历史用户检索空直跳，零影响。

- 摘要主路径：LLM 结构化提取（MEMORY_SUMMARY_PROMPT）；
  失败/非法输出降级确定性提取（正则实体 + 尾部对话粗摘要）
- 幂等：key = uuid5(conversation_id) 确定性——同一会话重复写 upsert 覆盖
- 旁路容错：maybe_write_memory / search_memories 永不向调用方抛错，失败只记日志
"""

from __future__ import annotations

import datetime as dt
import inspect
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.store.base import BaseStore
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from services.llm.client import get_chat_model
from services.llm.prompts import MEMORY_SUMMARY_PROMPT
from services.observability import metrics
from services.observability.token_tracker import phase_ainvoke
from services.rag.embedder import EMBEDDING_DIM, embed_texts

log = get_logger(__name__)

# 喂给摘要 LLM 的对话上限（条数 / 字符；保留尾部——最近的消息信息密度最高）
MAX_SUMMARY_MESSAGES = 40
MAX_SUMMARY_CHARS = 8000

# 读注入拼装的长度上限（Token 预算控制：约 600-800 token）
MAX_MEMORY_CONTEXT_CHARS = 1200

# 确定性实体提取（兜底路径与 LLM 实体的校验共用口径）
_POLICY_NO_RE = re.compile(r"POL-\d{4}-\d{4,}")
_AMOUNT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*[元万]")

# Store 命名空间：("memory", user_id)
_MEMORY_NAMESPACE = "memory"


class MemoryRecord(BaseModel):
    """一条会话记忆（写入 Store 的业务结构）。"""

    conversation_id: str
    user_id: str
    summary: str
    entities: dict[str, list[Any]] = Field(default_factory=dict)
    # 本记忆覆盖的用户轮数（HumanMessage 计数）
    turn_count: int = 0
    updated_at: str = ""
    # llm | fallback（摘要来源，用于质量观测）
    source: str = "llm"


def memory_key(conversation_id: str) -> str:
    """确定性 key：一会话一条记忆，upsert 覆盖实现幂等。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"claimflow:memory:{conversation_id}"))


# 兼容别名（T034 时代名称，tests / 外部可能引用）
memory_point_id = memory_key


def count_user_turns(messages: list[Any]) -> int:
    """用户轮数 = HumanMessage 条数（A06 每轮恰好追加一条）。"""
    return sum(1 for m in messages if isinstance(m, HumanMessage))


def format_messages_for_summary(messages: list[Any]) -> str:
    """消息列表 → 对话文本：过滤 ReAct 中间步（空 content 的纯 tool_calls 消息）。"""
    lines: list[str] = []
    for m in messages:
        content = str(m.content or "").strip()
        if not content:
            continue
        if isinstance(m, HumanMessage):
            lines.append(f"用户：{content}")
        elif isinstance(m, AIMessage):
            lines.append(f"助手：{content}")
        # ToolMessage / 系统消息不进摘要
    text = "\n".join(lines[-MAX_SUMMARY_MESSAGES:])
    return text[-MAX_SUMMARY_CHARS:]


def _norm_amount(raw: str) -> float | None:
    """金额归一化：去千分位 → float（失败返回 None）。"""
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def extract_entities_deterministic(text: str) -> dict[str, list[Any]]:
    """正则确定性提取实体（LLM 失败兜底；诊断名无法可靠正则，留空）。"""
    policy_nos = sorted(set(_POLICY_NO_RE.findall(text)))
    amounts = sorted({v for m in _AMOUNT_RE.findall(text) if (v := _norm_amount(m)) is not None})
    return {"policy_nos": policy_nos, "diagnoses": [], "amounts": amounts}


def _coerce_entities(raw: Any) -> dict[str, list[Any]]:
    """LLM 输出实体归一化：容忍字符串金额、去空去重。"""
    src = raw if isinstance(raw, dict) else {}
    policy_nos = sorted({str(x).strip() for x in (src.get("policy_nos") or []) if str(x).strip()})
    diagnoses = sorted({str(x).strip() for x in (src.get("diagnoses") or []) if str(x).strip()})
    amounts = sorted(
        {v for x in (src.get("amounts") or []) if (v := _norm_amount(str(x))) is not None}
    )
    return {"policy_nos": policy_nos, "diagnoses": diagnoses, "amounts": amounts}


def _parse_llm_json(raw: str) -> dict[str, Any] | None:
    """解析 LLM 输出的 JSON（容忍 markdown 代码块包裹，同 intent 节点口径）。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").lstrip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


async def summarize_conversation(
    messages: list[Any], *, conversation_id: str, user_id: str
) -> MemoryRecord:
    """生成会话记忆：LLM 结构化提取，失败降级确定性提取。本函数不抛错。"""
    transcript = format_messages_for_summary(messages)
    summary = ""
    entities = extract_entities_deterministic(transcript)
    source = "fallback"

    if transcript.strip():
        try:
            model = get_chat_model(temperature=0.0)
            prompt = MEMORY_SUMMARY_PROMPT.format(conversation=transcript)
            response = await phase_ainvoke(model, [HumanMessage(content=prompt)], phase="memory")
            parsed = _parse_llm_json(response.content or "")
            if parsed and str(parsed.get("summary", "")).strip():
                summary = str(parsed["summary"]).strip()
                entities = _coerce_entities(parsed.get("entities"))
                source = "llm"
            else:
                log.warning("memory_summary_invalid_output", raw=str(response.content or "")[:100])
        except Exception as exc:  # noqa: BLE001 摘要失败走兜底，不阻断
            log.warning("memory_summary_llm_error", error=str(exc)[:200])

    if not summary:
        tail = "；".join(ln for ln in transcript.split("\n") if ln.strip())[-300:]
        summary = f"【兜底摘要】{tail or '（空会话）'}"

    return MemoryRecord(
        conversation_id=conversation_id,
        user_id=user_id,
        summary=summary,
        entities=entities,
        turn_count=count_user_turns(messages),
        updated_at=dt.datetime.now().isoformat(timespec="seconds"),
        source=source,
    )


# ===== Store 存储层（T048） =====

_memory_store: BaseStore | None = None
_pg_setup_done = False


def _embed_for_store(texts: list[str]) -> list[list[float]]:
    """Store index 嵌入函数（BGE-M3 同源，1024 维）。"""
    return embed_texts(texts)


def _build_embed_text(record: MemoryRecord) -> str:
    """嵌入文本 = 摘要 + 实体字段（与 v1 口径一致）。"""
    ent = record.entities or {}
    extras: list[str] = []
    if ent.get("policy_nos"):
        extras.append("保单号：" + "、".join(str(x) for x in ent["policy_nos"]))
    if ent.get("diagnoses"):
        extras.append("诊断：" + "、".join(str(x) for x in ent["diagnoses"]))
    if ent.get("amounts"):
        extras.append("金额：" + "、".join(str(x) for x in ent["amounts"]))
    return record.summary + ("\n" + "\n".join(extras) if extras else "")


def get_memory_store() -> BaseStore:
    """记忆 Store 单例：dev=InMemoryStore / prod=AsyncPostgresStore（均带向量 index）。"""
    global _memory_store
    if _memory_store is None:
        index = {
            "dims": EMBEDDING_DIM,
            "embed": _embed_for_store,
            "fields": ["embed_text"],
        }
        if settings.app_profile.value == "prod":
            from langgraph.store.postgres import AsyncPostgresStore

            dsn = (
                f"postgresql://{settings.postgres_user}:{settings.postgres_password}"
                f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
            )
            _memory_store = AsyncPostgresStore(conn=dsn, index=index)  # type: ignore[call-arg]
            log.info("memory_store_initialized", backend="AsyncPostgresStore")
        else:
            from langgraph.store.memory import InMemoryStore

            _memory_store = InMemoryStore(index=index)  # type: ignore[call-arg]
            log.info("memory_store_initialized", backend="InMemoryStore", dim=EMBEDDING_DIM)
    return _memory_store


def reset_memory_store() -> None:
    """重置 Store 单例（测试用：换嵌入桩后重建）。"""
    global _memory_store, _pg_setup_done
    _memory_store = None
    _pg_setup_done = False


async def _ensure_pg_setup(store: BaseStore) -> None:
    """prod AsyncPostgresStore 首用时建表（幂等，一次）。"""
    global _pg_setup_done
    if _pg_setup_done or settings.app_profile.value != "prod":
        return
    setup = getattr(store, "setup", None)
    if setup is not None:
        result = setup()
        if inspect.isawaitable(result):
            await result
    _pg_setup_done = True


async def _store_put(record: MemoryRecord, embed_text: str) -> None:
    store = get_memory_store()
    await _ensure_pg_setup(store)
    value = {**record.model_dump(), "embed_text": embed_text}
    result = store.put((_MEMORY_NAMESPACE, record.user_id), memory_key(record.conversation_id), value)
    if inspect.isawaitable(result):
        await result


async def write_memory(record: MemoryRecord) -> None:
    """写入记忆 Store（确定性 key upsert，幂等覆盖）。"""
    await _store_put(record, _build_embed_text(record))
    log.info(
        "memory_written",
        conversation_id=record.conversation_id,
        user_id=record.user_id,
        turn_count=record.turn_count,
        source=record.source,
    )


async def maybe_write_memory(
    *,
    conversation_id: str,
    user_id: str,
    messages: list[Any],
    force: bool = False,
) -> bool:
    """A06 出口入口：轮数达到阈值（或会话终态 force）时更新该会话记忆。

    Returns: 是否发生写入。任何异常内部吞掉——记忆是旁路路径，不允许影响主对话流。
    """
    if not settings.memory_enabled:
        return False
    user_turns = count_user_turns(messages)
    if user_turns == 0:
        return False
    if not force and user_turns % settings.memory_summary_every_n_turns != 0:
        return False

    try:
        record = await summarize_conversation(
            messages, conversation_id=conversation_id, user_id=user_id
        )
        await write_memory(record)
        metrics.record_memory_write("success")
        return True
    except Exception as exc:  # noqa: BLE001 旁路失败静默（日志 + 指标）
        log.warning("memory_write_failed", conversation_id=conversation_id, error=str(exc)[:200])
        metrics.record_memory_write("error")
        return False


# ===== 读注入路径（T035） =====


@dataclass
class MemoryHit:
    """检索命中的一条历史会话记忆。"""

    conversation_id: str
    summary: str
    entities: dict[str, list[Any]]
    score: float
    updated_at: str


async def search_memories(query: str, user_id: str, top_k: int | None = None) -> list[MemoryHit]:
    """按 user_id 命名空间检索 top-k 历史会话记忆（低于 memory_min_score 的噪声过滤）。

    禁用 / 检索异常 → 空列表直跳（无历史用户零影响，永不抛错）。
    """
    if not settings.memory_enabled:
        return []
    try:
        store = get_memory_store()
        await _ensure_pg_setup(store)
        result = store.search(
            (_MEMORY_NAMESPACE, user_id),
            query=query,
            limit=top_k or settings.memory_top_k,
        )
        items = list(await result) if inspect.isawaitable(result) else list(result)
        results = [
            MemoryHit(
                conversation_id=str(item.value.get("conversation_id", "")),
                summary=str(item.value.get("summary", "")),
                entities=dict(item.value.get("entities") or {}),
                score=float(item.score),
                updated_at=str(item.value.get("updated_at", "")),
            )
            for item in items
            if float(item.score) >= settings.memory_min_score
        ]
        log.info(
            "memory_search_done",
            user_id=user_id,
            hits=len(results),
            scores=[round(h.score, 3) for h in results],
        )
        return results
    except Exception as exc:  # noqa: BLE001 读路径旁路，失败直跳（零影响）
        log.warning("memory_search_failed", user_id=user_id, error=str(exc)[:200])
        return []


def format_memory_context(hits: list[MemoryHit], max_chars: int = MAX_MEMORY_CONTEXT_CHARS) -> str:
    """命中记忆 → 注入文本（多条合并、总长截断——Token 预算控制）。"""
    text = "\n".join(f"- {h.summary}" for h in hits)
    return text[:max_chars]
