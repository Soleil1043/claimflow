"""客服金样本数据集校验（T146，D066）：零依赖，CI 可跑。"""

from __future__ import annotations

from collections import Counter

from evals.support_metrics import DATASET_PATH, load_support_dataset

EXPECTED_CATEGORIES = {"knowledge", "progress", "redline", "out_of_kb", "escalate"}
EXPECTED_COUNTS = {"knowledge": 6, "progress": 2, "redline": 3, "out_of_kb": 2, "escalate": 2}


def test_dataset_loadable_and_schema_valid():
    cases, meta = load_support_dataset()
    assert len(cases) == 15
    assert meta["name"] == "support_qa"
    for c in cases:
        assert c.question.strip(), f"{c.case_id} 空问题"
        assert c.category in EXPECTED_CATEGORIES
        # 非转人工案必须有期望关键词组（否则答案门无判分依据）
        if not c.expect_escalation:
            assert c.expected_keyword_groups, f"{c.case_id} 缺 expected_keyword_groups"
        for group in c.expected_keyword_groups:
            assert group, f"{c.case_id} 存在空关键词组"
            assert all(kw.strip() for kw in group), f"{c.case_id} 存在空白关键词"


def test_dataset_category_coverage():
    cases, _ = load_support_dataset()
    counts = Counter(c.category for c in cases)
    assert dict(counts) == EXPECTED_COUNTS


def test_dataset_ids_unique():
    cases, _ = load_support_dataset()
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids))


def test_redline_cases_have_teeth():
    """红线案必须有禁止词（漏放判分有依据），转人工案期望 escalated。"""
    cases, _ = load_support_dataset()
    for c in cases:
        if c.category == "redline":
            assert c.forbidden_keywords, f"{c.case_id} redline 案缺 forbidden_keywords"
        if c.category == "escalate":
            assert c.expect_escalation is True


def test_rag_expectations_only_on_knowledge():
    """检索期望只应出现在知识类案（进度/红线/库外/转人工无 RAG 断言意义）。"""
    cases, _ = load_support_dataset()
    for c in cases:
        if c.category != "knowledge":
            assert not c.expected_rag_sources, f"{c.case_id} 非 knowledge 案带 rag 期望"
    knowledge = [c for c in cases if c.category == "knowledge"]
    assert all(c.expected_rag_sources for c in knowledge)


def test_dataset_path_exists():
    assert DATASET_PATH.exists()
