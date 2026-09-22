"""客服判分纯函数单测（T146，D066）：零依赖，CI 可跑。"""

from __future__ import annotations

from evals.schemas import SupportQACase
from evals.support_metrics import (
    aggregate_support,
    score_forbidden,
    score_keywords,
    score_rag_sources,
    score_support_case,
)


def _case(**kwargs) -> SupportQACase:
    defaults = dict(
        case_id="SUP-T-001",
        category="knowledge",
        question="q",
        expected_keyword_groups=[["30 天", "30天"]],
        forbidden_keywords=[],
        expect_escalation=False,
        expected_rag_sources=[],
    )
    defaults.update(kwargs)
    return SupportQACase(**defaults)


class TestScoreKeywords:
    def test_group_and_semantics(self):
        # 组间 AND：两组都命中才过
        ok, missed = score_keywords("等待期 30 天，已受理 received", [["30 天"], ["received"]])
        assert ok and missed == []

    def test_group_or_semantics(self):
        # 组内 OR：任一命中即该组过
        ok, _ = score_keywords("免赔额可以抵扣", [["抵扣", "抵"], ["免赔"]])
        assert ok

    def test_missed_group_index(self):
        ok, missed = score_keywords("只有免赔", [["30 天", "30天"], ["免赔"]])
        assert not ok and missed == [0]

    def test_case_insensitive(self):
        ok, _ = score_keywords("案件状态 RECEIVED", [["received"]])
        assert ok

    def test_empty_groups_pass(self):
        ok, missed = score_keywords("任意内容", [])
        assert ok and missed == []


class TestScoreForbidden:
    def test_hit(self):
        assert score_forbidden("我们肯定能赔", ["肯定能赔", "包赔"]) == ["肯定能赔"]

    def test_clean(self):
        assert score_forbidden("以决定书为准", ["肯定能赔"]) == []


class TestScoreRagSources:
    def test_any_hit_passes(self):
        assert score_rag_sources(
            ["03-医疗险理赔规则手册.md", "08-常见问题FAQ材料与流程.md"],
            ["03-医疗险理赔规则手册"],
        )

    def test_miss(self):
        assert not score_rag_sources(["09-意外险理赔规则.md"], ["05-等待期规则详解"])

    def test_no_expectation_passes(self):
        assert score_rag_sources([], [])


class TestScoreSupportCase:
    def test_full_pass(self):
        case = _case()
        r = score_support_case(case, "医疗险等待期 30 天", "ai")
        assert r["passed"] and r["checks"] == {
            "keywords": True, "forbidden": True, "escalation": True,
        }
        assert r["rag_expected"] is False  # 无期望不计入命中率分母

    def test_escalation_mismatch_fails(self):
        case = _case(expect_escalation=True)
        r = score_support_case(case, "已为您转接人工坐席", "ai")
        assert not r["passed"] and r["checks"]["escalation"] is False

    def test_forbidden_leak_fails(self):
        case = _case(forbidden_keywords=["5000"])
        r = score_support_case(case, "5000 元以下会自动通过", "ai")
        assert not r["passed"] and r["forbidden_hits"] == ["5000"]

    def test_rag_observed_not_blocking(self):
        """检索未命中只降观测指标，不并入 passed（D066-2）。"""
        case = _case(expected_rag_sources=["05-等待期规则详解"])
        r = score_support_case(case, "等待期 30 天", "ai", retrieved_sources=["09-x.md"])
        assert r["passed"] is True and r["rag_hit"] is False


class TestAggregateSupport:
    def test_aggregate(self):
        cases = [
            _case(case_id="A"),
            _case(case_id="B", expected_keyword_groups=[["不存在词"]]),
            _case(case_id="C", expected_rag_sources=["05-x"]),
        ]
        results = [
            score_support_case(cases[0], "30 天", "ai"),
            score_support_case(cases[1], "没有关键词", "ai"),
            score_support_case(cases[2], "30 天", "ai", ["05-x.md"]),
        ]
        agg = aggregate_support(results)
        assert agg["total"] == 3 and agg["passed"] == 2
        assert agg["answer_pass_rate"] == round(2 / 3, 4)
        assert agg["rag_total"] == 1 and agg["rag_hit"] == 1
        assert len(agg["failures"]) == 1 and agg["failures"][0]["case_id"] == "B"

    def test_empty(self):
        agg = aggregate_support([])
        assert agg["total"] == 0 and agg["rag_hit_rate"] is None

    def test_all_pass_gate_value(self):
        results = [score_support_case(_case(), "30 天", "ai") for _ in range(3)]
        agg = aggregate_support(results)
        assert agg["answer_pass_rate"] == 1.0
