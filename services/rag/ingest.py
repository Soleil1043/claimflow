"""知识库文档分块与入库（F06）。

分块策略：markdown 按 ## 二级标题切块（标题 + 所属章节内容），
块内携带文档元数据（title / category / source_file）写入 Qdrant payload；
同时把文档级元数据（chunk_count / embedded_at）写入 kb_documents 表。

用法（同步脚本）：
    uv run python -m services.rag.ingest
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from qdrant_client import models

from app.core.config import settings
from app.core.logging import get_logger
from services.db.models import KbDocument
from services.db.session import get_session_factory
from services.rag.embedder import EMBEDDING_DIM, embed_texts
from services.rag.qdrant_client import get_qdrant_client

log = get_logger(__name__)

KB_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "kb_docs"

# 单块最大字符数（超出则按 ### 三级标题再切，仍超出按段落硬切）
MAX_CHUNK_CHARS = 800


@dataclass
class Chunk:
    """知识库文本块。"""

    text: str
    title: str
    category: str
    source_file: str
    chunk_index: int


def _category_of(title: str) -> str:
    """根据文档标题推断类别（入库元数据）。"""
    if "条款" in title:
        return "条款"
    if "免责" in title:
        return "免责说明"
    if "FAQ" in title or "常见问题" in title:
        return "常见问题"
    return "理赔规则"


def split_markdown(content: str, title: str, category: str, source_file: str) -> list[Chunk]:
    """按二级标题切块；超长块再按段落细切。"""
    # 提取一级标题（作为文档主题上下文附加到每块开头）
    h1 = re.search(r"^#\s+(.+)$", content, flags=re.MULTILINE)
    doc_title = h1.group(1).strip() if h1 else title

    # 按 ## 切分
    sections: list[tuple[str, str]] = []
    parts = re.split(r"^##\s+", content, flags=re.MULTILINE)
    for part in parts[1:]:  # parts[0] 是一级标题区（首部说明）
        lines = part.split("\n", 1)
        sec_title = lines[0].strip()
        sec_body = lines[1].strip() if len(lines) > 1 else ""
        sections.append((sec_title, sec_body))
    if not sections:
        sections = [("全文", content)]

    chunks: list[Chunk] = []
    for sec_title, sec_body in sections:
        full_text = f"# {doc_title}\n## {sec_title}\n{sec_body}".strip()
        if len(full_text) <= MAX_CHUNK_CHARS:
            chunks.append(Chunk(full_text, title, category, source_file, len(chunks)))
            continue
        # 超长：按段落硬切
        paras = [p for p in sec_body.split("\n\n") if p.strip()]
        buf = f"# {doc_title}\n## {sec_title}"
        for para in paras:
            candidate = f"{buf}\n{para}".strip()
            if len(candidate) > MAX_CHUNK_CHARS and buf != f"# {doc_title}\n## {sec_title}":
                chunks.append(Chunk(buf, title, category, source_file, len(chunks)))
                buf = f"# {doc_title}\n## {sec_title}\n{para}"
            else:
                buf = candidate
        if buf.strip():
            chunks.append(Chunk(buf.strip(), title, category, source_file, len(chunks)))
    return chunks


async def ingest_kb(force: bool = False) -> int:
    """全量入库：分块 → 向量化 → 写 Qdrant + kb_documents 元数据表。

    Args:
        force: True 时先删除既有 collection 重建（干净重灌）
    Returns:
        入库 chunk 总数
    """
    client = get_qdrant_client()
    collection = settings.qdrant_collection

    if force:
        await client.delete_collection(collection)
        log.info("kb_collection_deleted", collection=collection)

    # collection 不存在则创建（存在则复用，支持增量追加）
    if not await client.collection_exists(collection):
        await client.create_collection(
            collection_name=collection,
            vectors_config=models.VectorParams(size=EMBEDDING_DIM, distance=models.Distance.COSINE),
        )
        log.info("kb_collection_created", collection=collection, dim=EMBEDDING_DIM)

    total = 0
    md_files = sorted(KB_DIR.glob("*.md"))
    if not md_files:
        log.warning("kb_docs_empty", dir=str(KB_DIR))
        return 0

    for md_file in md_files:
        title = md_file.stem.split("-", 1)[-1]  # 去掉序号前缀
        category = _category_of(title)
        content = md_file.read_text(encoding="utf-8")
        chunks = split_markdown(content, title, category, md_file.name)
        if not chunks:
            continue

        vectors = embed_texts([c.text for c in chunks])
        points = [
            models.PointStruct(
                id=total + i,
                vector=vec,
                payload={
                    "title": c.title,
                    "category": c.category,
                    "source_file": c.source_file,
                    "chunk_index": c.chunk_index,
                    "text": c.text,
                },
            )
            for i, (c, vec) in enumerate(zip(chunks, vectors, strict=True))
        ]
        await client.upsert(collection_name=collection, points=points)
        total += len(chunks)
        log.info("kb_doc_ingested", file=md_file.name, chunks=len(chunks))

        # 文档元数据入 PG/SQLite
        factory = get_session_factory()
        async with factory() as session:
            existing = (
                await session.execute(
                    KbDocument.__table__.select().where(KbDocument.source_file == md_file.name)
                )
            ).first()
            now = dt.datetime.now()
            if existing is None:
                session.add(
                    KbDocument(
                        title=title,
                        source_file=md_file.name,
                        category=category,
                        chunk_count=len(chunks),
                        embedded_at=now,
                    )
                )
            else:
                await session.execute(
                    KbDocument.__table__.update()
                    .where(KbDocument.source_file == md_file.name)
                    .values(chunk_count=len(chunks), embedded_at=now, title=title, category=category)
                )
            await session.commit()

    log.info("kb_ingest_done", documents=len(md_files), chunks=total)
    return total


if __name__ == "__main__":
    import asyncio

    asyncio.run(ingest_kb(force=True))
