"""claim_rule_rag 工具与 RAG 服务层测试。

分两层：
- split_markdown 纯函数测试（真实执行）
- 检索链路测试：mock embedder（避免加载 2GB 模型）+ 临时 Qdrant local mode，
  用假向量验证入库/检索/排序链路正确性
"""

from __future__ import annotations

from pathlib import Path

import pytest

import services.rag.embedder as embedder_module
import services.rag.qdrant_client as qdrant_module
from services.rag.ingest import KB_DIR, split_markdown
from services.rag.retriever import search_kb
from tools.claim.claim_rule_rag import ClaimRuleRagTool

# ---------- 分块纯函数 ----------


def test_kb_docs_exist() -> None:
    """知识库文档 10-20 篇（F06 验收：12 篇）。"""
    md_files = list(KB_DIR.glob("*.md"))
    assert 10 <= len(md_files) <= 20


def test_split_markdown_by_sections() -> None:
    """按二级标题切块，每块携带文档主题上下文。"""
    content = "# 测试文档\n\n总说明\n\n## 甲节\n内容甲\n\n## 乙节\n内容乙"
    chunks = split_markdown(content, "测试文档", "理赔规则", "test.md")
    assert len(chunks) == 2
    assert "测试文档" in chunks[0].text  # 一级标题作为上下文附加
    assert "甲节" in chunks[0].text
    assert "乙节" in chunks[1].text
    assert chunks[0].chunk_index == 0
    assert chunks[1].chunk_index == 1


def test_split_markdown_long_section_split() -> None:
    """超长二级节按段落细切且不超过上限太多。"""
    long_body = "\n\n".join(f"段落{i}" + "字" * 100 for i in range(20))
    content = f"# 长文档\n\n## 长节\n{long_body}"
    chunks = split_markdown(content, "长文档", "条款", "long.md")
    assert len(chunks) > 1
    assert all(len(c.text) <= 800 + 300 for c in chunks)  # 允许段落级少量超出


def test_split_markdown_no_sections() -> None:
    """无二级标题时整篇一块。"""
    chunks = split_markdown("# 只有标题\n正文一段", "文档", "条款", "a.md")
    assert len(chunks) == 1


# ---------- 检索链路（mock embedding + 临时 Qdrant local mode） ----------


@pytest.fixture()
async def rag_env(tmp_path, monkeypatch):
    """临时 local mode Qdrant + 固定向量 mock，灌入 3 个 chunk。

    mock 策略：embed_query 与 embed_texts 都用确定性哈希向量——
    查询词包含某 chunk 的关键词时，向量与其对齐（用词频驱动相似度）。
    """
    # 固定向量构造：关键词 → 基向量
    keyword_vectors = {
        "阑尾炎": [1.0, 0.0, 0.0, 0.0],
        "材料": [0.0, 1.0, 0.0, 0.0],
        "免责": [0.0, 0.0, 1.0, 0.0],
    }
    texts = {
        "t_appendix": "阑尾炎手术等待期说明……",
        "t_material": "理赔申请材料清单……",
        "t_exclusion": "既往症免责条款……",
    }
    text_vectors = {
        "t_appendix": keyword_vectors["阑尾炎"],
        "t_material": keyword_vectors["材料"],
        "t_exclusion": keyword_vectors["免责"],
    }

    def fake_embed_query(text: str) -> list[float]:
        for kw, vec in keyword_vectors.items():
            if kw in text:
                return vec
        return [0.5, 0.5, 0.5, 0.5]

    def fake_embed_texts(chunk_texts: list[str]) -> list[list[float]]:
        return [text_vectors.get(t, [0.5, 0.5, 0.5, 0.5]) for t in chunk_texts]

    monkeypatch.setattr(embedder_module, "embed_query", fake_embed_query)
    monkeypatch.setattr(embedder_module, "embed_texts", fake_embed_texts)

    # 检索模块与入库模块都通过 services.rag.embedder 引用，同步替换
    import services.rag.ingest as ingest_module
    import services.rag.retriever as retriever_module

    monkeypatch.setattr(retriever_module, "embed_query", fake_embed_query)
    monkeypatch.setattr(ingest_module, "embed_texts", fake_embed_texts)
    # 入库维度校验也 mock 掉（假向量是 4 维）
    monkeypatch.setattr(ingest_module, "EMBEDDING_DIM", 4)

    # 临时 local mode 客户端
    fake_client = qdrant_module.AsyncQdrantClient(path=str(tmp_path / "qdrant"))
    monkeypatch.setattr(qdrant_module, "get_qdrant_client", lambda: fake_client)
    monkeypatch.setattr(retriever_module, "get_qdrant_client", lambda: fake_client)
    monkeypatch.setattr(ingest_module, "get_qdrant_client", lambda: fake_client)

    # 直接灌数据（绕过 ingest 的文档扫描，控制变量）
    from qdrant_client import models

    await fake_client.create_collection(
        collection_name="claim_rules",
        vectors_config=models.VectorParams(size=4, distance=models.Distance.COSINE),
    )
    points = [
        models.PointStruct(
            id=i,
            vector=vec,
            payload={"title": t, "category": "理赔规则", "source_file": f"{t}.md", "chunk_index": 0, "text": texts[t]},
        )
        for i, (t, vec) in enumerate(text_vectors.items())
    ]
    await fake_client.upsert(collection_name="claim_rules", points=points)

    yield
    await fake_client.close()


async def test_search_ranks_relevant_chunk_first(rag_env) -> None:
    """检索'阑尾炎等待期'：阑尾炎相关 chunk 排第一（相似度降序）。"""
    results = await search_kb("阑尾炎手术有等待期吗", top_k=3)
    assert len(results) == 3
    assert results[0].title == "t_appendix"
    assert results[0].score >= results[1].score >= results[2].score  # 降序


async def test_search_material_query(rag_env) -> None:
    """检索'理赔材料'：材料清单 chunk 排第一。"""
    results = await search_kb("理赔需要什么材料", top_k=2)
    assert results[0].title == "t_material"


async def test_search_empty_collection_returns_empty(tmp_path, monkeypatch) -> None:
    """collection 不存在：返回空列表不抛错（工具层转为 success=False）。"""
    import services.rag.retriever as retriever_module

    fake_client = qdrant_module.AsyncQdrantClient(path=str(tmp_path / "empty"))
    monkeypatch.setattr(retriever_module, "get_qdrant_client", lambda: fake_client)
    monkeypatch.setattr(retriever_module, "embed_query", lambda _: [0.1, 0.2, 0.3, 0.4])
    results = await search_kb("任意查询")
    assert results == []
    await fake_client.close()


async def test_claim_rule_rag_tool_success(rag_env) -> None:
    """工具层：检索成功返回 results 列表（含 score/title/text）。"""
    tool = ClaimRuleRagTool()
    result = await tool.execute({"query": "阑尾炎有等待期吗", "top_k": 2})
    assert result.success is True
    assert len(result.data["results"]) == 2
    first = result.data["results"][0]
    assert first["title"] == "t_appendix"
    assert "score" in first and "text" in first and "source_file" in first


async def test_claim_rule_rag_tool_empty_kb(rag_env, monkeypatch) -> None:
    """工具层：知识库无结果时 success=False。"""


    async def fake_search(query: str, top_k: int = 4) -> list:
        return []

    monkeypatch.setattr("tools.claim.claim_rule_rag.search_kb", fake_search)
    result = await ClaimRuleRagTool().execute({"query": "任意"})
    assert result.success is False
    assert "无结果" in (result.error_message or "")


def test_openai_tool_definition() -> None:
    """工具 schema 符合 function calling 格式。"""
    definition = ClaimRuleRagTool().to_openai_tool()
    fn = definition["function"]
    assert fn["name"] == "claim_rule_rag"
    assert "知识库" in fn["description"]
    assert set(fn["parameters"]["properties"]) == {"query", "top_k"}


def test_kb_dir_path_resolves() -> None:
    """KB_DIR 指向项目 data/kb_docs。"""
    assert KB_DIR.name == "kb_docs"
    assert Path(KB_DIR).exists()
