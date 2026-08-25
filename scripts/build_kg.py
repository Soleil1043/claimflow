"""知识图谱构建脚本（T031）。

用法：uv run python -m scripts.build_kg

流程：遍历 data/kb_docs/*.md → LLM 逐篇抽取三元组（容错：解析失败重试 1 次，
再失败跳过该篇）→ build_graph_from_triples 汇总去重 → 落盘
data/graph/claim_rules_kg.json（幂等：每次全量重建覆盖）。
本脚本只用 LLM，不触 Qdrant/BGE，无需 HF_HUB_OFFLINE。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.core.logging import configure_logging, get_logger
from services.llm.client import get_chat_model
from services.llm.prompts import KG_EXTRACTION_PROMPT
from services.observability.llm_metrics import observed_ainvoke
from services.rag.knowledge_graph import (
    GRAPH_PATH,
    build_graph_from_triples,
    save_graph,
)

log = get_logger(__name__)

KB_DIR = Path("data/kb_docs")


def _parse_triples(raw: str) -> list[dict[str, Any]] | None:
    """解析 LLM 输出的三元组 JSON 数组（容忍 markdown 包裹）。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


async def extract_from_doc(model: Any, doc_path: Path) -> list[dict[str, Any]]:
    """单篇文档抽取：LLM → 三元组列表（失败重试 1 次，再失败返回空）。"""
    doc_text = doc_path.read_text(encoding="utf-8")
    # 超长文档截断（防 Token 失控，kb_docs 均短小，此为防御）
    if len(doc_text) > 8000:
        doc_text = doc_text[:8000] + "…（截断）"

    prompt = KG_EXTRACTION_PROMPT.format(source_file=doc_path.name, doc_text=doc_text)
    for attempt in range(2):
        try:
            response = await observed_ainvoke(model, [prompt])
            triples = _parse_triples(response.content or "")
            if triples is not None:
                return triples
            log.warning("kg_extract_unparsed", file=doc_path.name, attempt=attempt)
        except Exception as exc:  # noqa: BLE001 抽取失败重试
            log.warning("kg_extract_error", file=doc_path.name, attempt=attempt, error=str(exc)[:150])
    log.warning("kg_extract_skipped", file=doc_path.name)
    return []


async def main() -> None:
    configure_logging()
    docs = sorted(KB_DIR.glob("*.md"))
    print(f"待抽取文档：{len(docs)} 篇")

    model = get_chat_model(temperature=0.0)
    all_triples: list[dict[str, Any]] = []
    for i, doc in enumerate(docs, 1):
        triples = await extract_from_doc(model, doc)
        all_triples.extend(triples)
        print(f"[{i:>2}/{len(docs)}] {doc.name}: {len(triples)} 条三元组")

    data = build_graph_from_triples(all_triples, source_files=[d.name for d in docs])
    save_graph(data, GRAPH_PATH)

    print(f"\n图谱已写入 {GRAPH_PATH}")
    print(f"三元组总数：{len(all_triples)}（实体合并前）")
    print(f"实体数：{len(data.entities)}，关系数：{len(data.relations)}")


if __name__ == "__main__":
    asyncio.run(main())
