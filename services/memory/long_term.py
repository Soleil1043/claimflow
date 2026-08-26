"""长期记忆写路径（T034，architecture.md 6.3 三层记忆）。

会话累计 N 轮（用户消息数）时生成对话摘要 + 关键实体（保单号/诊断/金额），
BGE-M3 向量化写入 Qdrant 独立 collection，payload 携带 user_id 实现用户隔离
（读路径按 user_id filter 注入 system prompt 由 T035 实现）。

- 摘要主路径：LLM 结构化提取（MEMORY_SUMMARY_PROMPT）；
  失败/非法输出降级确定性提取（正则实体 + 尾部对话粗摘要）
- 幂等：point id = uuid5(conversation_id) 确定性——同一会话重复写 upsert 覆盖
  （摘要始终反映该会话最新全貌），不产生重复条目
- 旁路容错：maybe_write_memory 永不向调用方（A06）抛错，失败只记日志与指标
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field
from qdrant_client import models

from app.core.config import settings
from app.core.logging import get_logger
from services.llm.client import get_chat_model
from services.llm.prompts import MEMORY_SUMMARY_PROMPT
from services.observability import metrics
from services.observability.token_tracker import phase_ainvoke
from services.rag.embedder import EMBEDDING_DIM, embed_texts
from services.rag.qdrant_client import get_qdrant_client

log = get_logger(__name__)

# 喂给摘要 LLM 的对话上限（条数 / 字符；保留尾部——最近的消息信息密度最高）
MAX_SUMMARY_MESSAGES = 40
MAX_SUMMARY_CHARS = 8000

# 确定性实体提取（兜底路径与 LLM 实体的校验共用口径）
_POLICY_NO_RE = re.compile(r"POL-\d{4}-\d{4,}")
_AMOUNT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*[元万]")


class MemoryRecord(BaseModel):
    """一条会话记忆（写入 Qdrant 的业务结构）。"""

    conversation_id: str
    user_id: str
    summary: str
    entities: dict[str, list[Any]] = Field(default_factory=dict)
    # 本记忆覆盖的用户轮数（HumanMessage 计数）
    turn_count: int = 0
    updated_at: str = ""
    # llm | fallback（摘要来源，用于质量观测）
    source: str = "llm"


def memory_point_id(conversation_id: str) -> str:
    """确定性 point id：一会话一条记忆，upsert 覆盖实现幂等。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"claimflow:memory:{conversation_id}"))


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


async def write_memory(record: MemoryRecord) -> None:
    """向量化并写入 Qdrant 记忆 collection（确定性 id upsert，幂等覆盖）。"""
    client = get_qdrant_client()
    collection = settings.qdrant_memory_collection
    if not await client.collection_exists(collection):
        await client.create_collection(
            collection_name=collection,
            vectors_config=models.VectorParams(size=EMBEDDING_DIM, distance=models.Distance.COSINE),
        )
        log.info("memory_collection_created", collection=collection, dim=EMBEDDING_DIM)

    # 嵌入文本 = 摘要 + 实体字段（实体入向量，保证"我上次问的那张保单"类实体查询可命中）
    ent = record.entities or {}
    extras: list[str] = []
    if ent.get("policy_nos"):
        extras.append("保单号：" + "、".join(str(x) for x in ent["policy_nos"]))
    if ent.get("diagnoses"):
        extras.append("诊断：" + "、".join(str(x) for x in ent["diagnoses"]))
    if ent.get("amounts"):
        extras.append("金额：" + "、".join(str(x) for x in ent["amounts"]))
    embed_text = record.summary + ("\n" + "\n".join(extras) if extras else "")

    vector = embed_texts([embed_text])[0]
    await client.upsert(
        collection_name=collection,
        points=[
            models.PointStruct(
                id=memory_point_id(record.conversation_id),
                vector=vector,
                payload=record.model_dump(),
            )
        ],
    )
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
