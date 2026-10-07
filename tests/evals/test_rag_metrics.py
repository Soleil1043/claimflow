"""RAG 评测度量单测（T161，D075）：gold 有效性 / 判召回数学 / JSON 解析健壮性。"""

from __future__ import annotations

import json
from pathlib import Path

from evals.rag_metrics import (
    _parse_json_array,
    compute_recall_metrics,
    gold_rank,
)
from evals.schemas import RagQACase, RagQADataset
from services.rag.retriever import RetrievedChunk

QA_PATH = Path(__file__).resolve().parents[2] / "evals" / "datasets" / "rag_qa_liability.json"
KB_DIR = Path(__file__).resolve().parents[2] / "data" / "kb_docs"


def _qa(idx: int = 0) -> RagQACase:
    dataset = RagQADataset.model_validate(json.loads(QA_PATH.read_text(encoding="utf-8")))
    return dataset.cases[idx]


def test_qa_dataset_loads() -> None:
    dataset = RagQADataset.model_validate(json.loads(QA_PATH.read_text(encoding="utf-8")))
    assert len(dataset.cases) >= 20
    assert len({c.id for c in dataset.cases}) == len(dataset.cases)
    assert {c.category for c in dataset.cases} <= {
        "coverage",
        "exclusion",
        "waiting",
        "amount",
        "icd",
        "catalog",
        "process",
    }


def test_gold_markers_exist_in_source_docs() -> None:
    """gold_marker 必须逐字存在于对应 kb_docs 源文档——文档改写后此测试先红。"""
    dataset = RagQADataset.model_validate(json.loads(QA_PATH.read_text(encoding="utf-8")))
    for c in dataset.cases:
        text = (KB_DIR / c.gold_source_file).read_text(encoding="utf-8")
        assert c.gold_marker in text, f"{c.id}: marker 与源文档失配（{c.gold_source_file}）"


def _chunk(source_file: str, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        text=text, title="t", category="理赔规则", source_file=source_file, score=0.9
    )


def test_gold_rank_hit_and_miss() -> None:
    qa = _qa()
    chunks = [
        _chunk("other.md", "无关内容"),
        _chunk(qa.gold_source_file, f"标题\n{qa.gold_marker}\n正文"),
        _chunk(qa.gold_source_file, "同文件但不含标记"),
    ]
    assert gold_rank(chunks, qa) == 2
    assert (
        gold_rank(list(reversed(chunks)), qa) is None
        or gold_rank([c for c in reversed(chunks) if c.source_file == qa.gold_source_file], qa)
        is not None
    )
    assert gold_rank([_chunk("zz.md", qa.gold_marker)], qa) is None  # source_file 不符


def test_recall_metrics_math() -> None:
    ranks = [1, None, 3, 2, 5, 8, None, 1]
    m = compute_recall_metrics(ranks, ks=(1, 2, 4, 8))
    assert m["recall_at_1"] == 0.25  # 两个 1
    assert m["recall_at_2"] == 0.375  # 1,1,2 → 3/8
    assert m["recall_at_4"] == 0.5  # 1,3,2,1 → 4/8
    assert m["recall_at_8"] == 0.75  # 除两个 None 外全中
    # MRR = (1 + 1/3 + 1/2 + 1/5 + 1/8 + 1) / 8
    expected = (1.0 + 1 / 3 + 0.5 + 0.2 + 0.125 + 1.0) / 8
    assert abs(m["mrr"] - expected) < 1e-3


def test_recall_metrics_empty() -> None:
    m = compute_recall_metrics([], ks=(1, 4))
    assert m == {"recall_at_1": 0.0, "recall_at_4": 0.0, "mrr": 0.0}


def test_parse_json_array_robust() -> None:
    assert _parse_json_array("[1, 0, 1]") == [1, 0, 1]
    assert _parse_json_array('```json\n["a", "b"]\n```') == ["a", "b"]
    assert _parse_json_array('前缀杂讯 ["x"] 后缀') == ["x"]
    try:
        _parse_json_array('{"not": "a list"}')
        raise AssertionError("应拒绝非数组")
    except (ValueError, json.JSONDecodeError):
        pass
