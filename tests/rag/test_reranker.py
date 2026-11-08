"""重排序精排测试（T043；T221 恢复）。

mock CrossEncoder（避免加载 2.2GB 模型）：覆盖 rerank_chunks 排序/截取/失败回退。

恢复说明（T221/I20）：本文件随 T093 v1 咨询代码清理（commit 54b2b4f）整体删除。
rerank_chunks 仍是在库服务（scripts/verify_rerank 使用，D020 预留能力），三条纯逻辑
用例原样恢复；原 rag_node 开关语义用例依赖已删除的 v1 nodes/rag.py，在线检索链路
（claim_rule_rag）当前不接精排，故不恢复。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

import services.rag.reranker as reranker_module

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


def _enable_fake_reranker(monkeypatch: pytest.MonkeyPatch) -> None:
    """注入假打分器 + 打开开关（普通 helper，供各用例按需调用）。"""
    monkeypatch.setattr(reranker_module, "rerank_scores", _fake_scores_for)
    monkeypatch.setattr(reranker_module.settings, "rerank_enabled", True)


# ---------- rerank_chunks 纯逻辑 ----------


def test_rerank_orders_and_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_rerank_failure_falls_back_to_vector_order(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_rerank_empty_candidates() -> None:
    """空候选：直通，不调用模型。"""
    out, reranked = reranker_module.rerank_chunks("任意", [], top_k=4)
    assert out == [] and reranked is False
