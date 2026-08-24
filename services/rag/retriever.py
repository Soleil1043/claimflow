"""知识库检索服务（F06）。

查询 → BGE-M3 向量化 → Qdrant 相似度检索 top-k，
返回带分数排序的条款片段。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.core.logging import get_logger
from services.rag.embedder import embed_query
from services.rag.qdrant_client import get_qdrant_client

log = get_logger(__name__)


@dataclass
class RetrievedChunk:
    """检索结果片段。"""

    text: str
    title: str
    category: str
    source_file: str
    score: float  # 相似度（cosine，越大越相关）


async def search_kb(query: str, top_k: int = 4) -> list[RetrievedChunk]:
    """检索知识库：返回按相似度降序的 top-k 片段。"""
    client = get_qdrant_client()
    collection = settings.qdrant_collection

    if not await client.collection_exists(collection):
        log.warning("kb_collection_missing", collection=collection)
        return []

    vector = embed_query(query)
    response = await client.query_points(
        collection_name=collection,
        query=vector,
        limit=top_k,
        with_payload=True,
    )
    hits = response.points

    results = [
        RetrievedChunk(
            text=str(p.payload["text"]),
            title=str(p.payload["title"]),
            category=str(p.payload["category"]),
            source_file=str(p.payload["source_file"]),
            score=float(p.score),
        )
        for p in hits
    ]
    log.info("kb_search_done", query=query[:50], hits=len(results), top_scores=[r.score for r in results[:3]])
    return results
