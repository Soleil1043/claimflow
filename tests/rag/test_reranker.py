"""重排序精排测试（T043）。

mock CrossEncoder（避免加载 2.2GB 模型）：覆盖 rerank_chunks 排序/截取/失败回退，
以及 rag_node 开关语义（开 = 多召回 + 重排生效；关 = 与 T021 行为完全一致）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

import services.memory.long_term  # noqa: F401 确保 conftest 语义完整
import services.rag.reranker as reranker_module
from nodes.rag import rag_node
from services.rag.retriever import RetrievedChunk

# ---------- 测试替身 ----------


@dataclass
class _Chunk:
    """轻量候选（模拟 RetrievedChunk，score 可变）。"""

    text: str
    title: str = ""
    category: str = ""
    source_file: str = ""
    score: float = 0.0
    extra: dict = field(default_factory=dict)


def _fake_scores_for(query: str, texts: list[str]) -> list[float]:
    """确定性打分：含"等待期"的文档得高分（mock CrossEncoder 的排序语义）。"""
    return [3.0 if "等待期" in t else (1.0 if "免赔" in t else 0.0) for t in texts]


def _enable_fake_reranker(monkeypatch: pytest.MonkeyPatch):
    """注入假打分器 + 打开开关（普通 helper，供各用例按需调用）。"""
    monkeypatch.setattr(reranker_module, "rerank_scores", _fake_scores_for)
    monkeypatch.setattr(reranker_module.settings, "rerank_enabled", True)


# ---------- rerank_chunks 纯逻辑 ----------


async def test_rerank_orders_and_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    """排序按分数降序 + 截取 top_k；score 被覆盖为精排分。"""
    _enable_fake_reranker(monkeypatch)
    chunks = [
        _Chunk(text="免赔额说明", score=0.9),
        _Chunk(text="等待期规则详解", score=0.5),
        _Chunk(text="理赔流程", score=0.3),
    ]
    out, reranked = reranker_module.rerank_chunks("等待期是多久", chunks, top_k=2)
    assert reranked is True
    assert [c.text for c in out] == ["等待期规则详解", "免赔额说明"]
    assert out[0].score == 3.0  # 精排分覆盖原向量分
    assert len(out) == 2


async def test_rerank_failure_falls_back_to_vector_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """打分故障：回退向量序截取 top_k，reranked=False（零影响语义）。"""
    _enable_fake_reranker(monkeypatch)

    def broken(query: str, texts: list[str]) -> list[float]:
        raise RuntimeError("model down")

    monkeypatch.setattr(reranker_module, "rerank_scores", broken)
    chunks = [_Chunk(text=f"d{i}", score=0.9 - i * 0.1) for i in range(5)]
    out, reranked = reranker_module.rerank_chunks("任意", chunks, top_k=3)
    assert reranked is False
    assert [c.score for c in out] == [0.9, 0.8, 0.7]  # 原向量序
    assert len(out) == 3


async def test_rerank_empty_candidates() -> None:
    """空候选：直通，不调用模型。"""
    out, reranked = reranker_module.rerank_chunks("任意", [], top_k=4)
    assert out == [] and reranked is False


# ---------- rag_node 开关语义 ----------


def _patch_search(monkeypatch: pytest.MonkeyPatch, captured: list[int]) -> None:
    """mock search_kb：记录召回条数，返回 6 个稳定候选（等待期主题排第 3）。"""
    pool = [
        RetrievedChunk(
            text=f"条款{i}",
            title=f"t{i}",
            category="理赔规则",
            source_file="f",
            score=0.9 - i * 0.1,
        )
        for i in range(6)
    ]
    pool[2] = RetrievedChunk(
        text="条款2：等待期 30 天规则", title="t2", category="理赔规则", source_file="f", score=0.7
    )

    async def fake_search_kb(query: str, top_k: int = 4) -> list:
        captured.append(top_k)
        return pool[:top_k]

    monkeypatch.setattr("nodes.rag.search_kb", fake_search_kb)


@pytest.fixture()
def rag_state() -> dict[str, Any]:
    from langchain_core.messages import HumanMessage

    return {"messages": [HumanMessage(content="等待期是多久")]}


async def test_rag_node_rerank_on(rag_state, monkeypatch) -> None:
    """开关开：召回 8（≥6 全取）→ 重排把等待期条款排到第一。"""
    captured: list[int] = []
    _patch_search(monkeypatch, captured)
    _enable_fake_reranker(monkeypatch)
    monkeypatch.setattr(reranker_module.settings, "rerank_recall_k", 8)

    result = await rag_node(rag_state)
    ctx = result["shared_data"]["rag_context"]
    assert captured == [8]  # 召回数变为 rerank_recall_k
    assert ctx["results"][0]["text"].startswith("条款2")  # 精排把等待期条款排第一
    assert ctx["results"][0]["score"] == 3.0
    assert len(ctx["results"]) == 4


async def test_rag_node_rerank_off_unchanged(rag_state, monkeypatch) -> None:
    """开关关：召回 4、不重排、结果与 T021 行为一致（条款0 向量分最高排第一）。"""
    captured: list[int] = []
    _patch_search(monkeypatch, captured)
    monkeypatch.setattr(reranker_module.settings, "rerank_enabled", False)

    result = await rag_node(rag_state)
    ctx = result["shared_data"]["rag_context"]
    assert captured == [4]
    assert ctx["results"][0]["text"] == "条款0"
    assert len(ctx["results"]) == 4


async def test_rag_node_rerank_error_not_fatal(rag_state, monkeypatch) -> None:
    """精排故障：rag_node 整体不抛错，回退向量序结果。"""
    captured: list[int] = []
    _patch_search(monkeypatch, captured)
    monkeypatch.setattr(reranker_module.settings, "rerank_enabled", True)
    monkeypatch.setattr(reranker_module.settings, "rerank_recall_k", 8)

    def broken(query: str, texts: list[str]) -> list[float]:
        raise RuntimeError("model down")

    monkeypatch.setattr(reranker_module, "rerank_scores", broken)

    result = await rag_node(rag_state)  # 不抛错
    ctx = result["shared_data"]["rag_context"]
    assert ctx["results"][0]["text"] == "条款0"  # 回退向量序
    assert len(ctx["results"]) == 4
