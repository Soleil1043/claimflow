"""RAG 检索节点（T021，F02 完整版）。

simple_faq 路径：用户知识类问题 → 知识库检索 top-k →
结果写入 shared_data["rag_context"] → synthesize 节点基于检索上下文生成回答。

检索失败 / 空结果不抛错：shared_data 留空标记，synthesize 兜底话术引导用户。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage

from app.core.logging import get_logger
from services.rag.retriever import search_kb
from state import AgentState

log = get_logger(__name__)

# 检索片段数（synthesize 上下文足够，Token 可控）
_RAG_TOP_K = 4


async def rag_node(state: AgentState) -> dict[str, Any]:
    """RAG 节点：读末尾用户问题 → 检索知识库 → 写 shared_data.rag_context。"""
    messages = state.get("messages") or []
    query = next(
        (m.content for m in reversed(messages) if isinstance(m, HumanMessage)), ""
    )
    query = str(query).strip()

    shared: dict[str, Any] = dict(state.get("shared_data") or {})

    if not query:
        shared["rag_context"] = {"summary": "空输入，未执行检索"}
        return {"shared_data": shared}

    try:
        chunks = await search_kb(query=query, top_k=_RAG_TOP_K)
    except Exception as exc:  # noqa: BLE001 检索故障不阻断：synthesize 走兜底
        log.warning("rag_node_error", error=str(exc)[:200])
        chunks = []

    if not chunks:
        log.info("rag_node_empty", query=query[:50])
        shared["rag_context"] = {
            "summary": "知识库检索无结果",
            "note": "未检索到相关条款，请基于通用理赔知识谨慎回答并提示以条款为准",
            "results": [],
        }
        return {"shared_data": shared}

    shared["rag_context"] = {
        "summary": f"知识库检索到 {len(chunks)} 条相关条款",
        "results": [
            {
                "text": c.text,
                "title": c.title,
                "category": c.category,
                "source_file": c.source_file,
                "score": round(c.score, 4),
            }
            for c in chunks
        ],
    }
    log.info("rag_node_done", query=query[:50], hits=len(chunks))
    return {"shared_data": shared}
