"""T027 评测指标计算测试：判分规则、聚合口径、运行器适配。"""

from __future__ import annotations

from evals.metrics import CaseResult, aggregate, result_from_a06, score_case
from evals.schemas import EvalCase, EvalCategory


def _case(**kwargs: object) -> EvalCase:
    """构造测试用例（默认只考核 must_include）。"""
    base = {
        "id": "T-001",
        "category": EvalCategory.SIMPLE_FAQ,
        "user_input": "测试输入",
        "must_include": ["4640"],
    }
    base.update(kwargs)  # type: ignore[arg-type]
    return EvalCase.model_validate(base)


def _result(**kwargs: object) -> CaseResult:
    base = {"case_id": "T-001", "category": EvalCategory.SIMPLE_FAQ}
    base.update(kwargs)  # type: ignore[arg-type]
    return CaseResult.model_validate(base)


# ===== score_case 判分规则 =====


def test_must_include_all_hit() -> None:
    case = _case(must_include=["4640", "免赔"])
    r = score_case(case, _result(answer="扣除免赔后可赔 4640 元"))
    assert r.passed and r.must_include_hit


def test_must_include_partial_miss() -> None:
    case = _case(must_include=["4640", "免赔"])
    r = score_case(case, _result(answer="可赔 4640 元"))
    assert not r.passed and not r.must_include_hit


def test_must_include_whitespace_normalized() -> None:
    """归一化匹配：'4,640 元' 与 '30 天' 空格差异不影响命中。"""
    case = _case(must_include=["30天"])
    r = score_case(case, _result(answer="等待期为 30 天"))
    assert r.must_include_hit


def test_any_of_single_hit() -> None:
    case = _case(must_include=[], any_of=["不承担", "不能赔", "不予赔付"])
    r = score_case(case, _result(answer="等待期内确诊的疾病不予赔付"))
    assert r.any_of_hit and r.passed


def test_any_of_all_miss() -> None:
    case = _case(must_include=[], any_of=["不承担", "不能赔"])
    r = score_case(case, _result(answer="可以正常理赔"))
    assert not r.any_of_hit and not r.passed


def test_must_not_include_violation() -> None:
    """命中违规话术直接失败（合规红线）。"""
    case = _case(must_include=[], must_not_include=["保证赔付", "肯定赔"])
    r = score_case(case, _result(answer="我们保证赔付您的损失"))
    assert not r.must_not_include_clean and not r.passed


def test_tool_match_subset_semantics() -> None:
    """期望工具是子集即可（多调不扣分，漏调失败）。"""
    case = _case(must_include=["保额"], expected_tools=["policy_query"])
    r = score_case(case, _result(answer="保额 ok", used_tools=["policy_query", "claim_calculator"]))
    assert r.tool_match and r.passed
    r2 = score_case(case, _result(answer="保额 ok", used_tools=["claim_rule_rag"]))
    assert not r2.tool_match and not r2.passed


def test_human_intervention_mismatch() -> None:
    """期望转人工但系统未转 → 失败。"""
    case = _case(must_include=["抱歉"], expect_human_intervention=True)
    r = score_case(case, _result(answer="抱歉 ok", need_human_intervention=False))
    assert not r.human_match and not r.passed


def test_error_blocks_pass() -> None:
    """运行异常的用例不通过，即使 answer 碰巧含关键词。"""
    case = _case()
    r = score_case(case, _result(answer="4640", error="LLM timeout"))
    assert not r.passed


# ===== aggregate 聚合 =====


def test_aggregate_basic() -> None:
    results = [
        _result(passed=True, compliance_status="PASS", duration_s=2.0),
        _result(passed=True, compliance_status="PASS", duration_s=4.0),
        _result(passed=False, compliance_status="MODIFIED", duration_s=6.0),
    ]
    report = aggregate(results)
    assert report.total == 3
    assert report.passed == 2
    assert report.task_completion_rate == 2 / 3
    assert report.compliance_pass_rate == 2 / 3
    assert report.avg_duration_s == 4.0
    assert len(report.failures) == 1
    assert report.by_category["simple_faq"]["total"] == 3.0


def test_aggregate_by_category() -> None:
    results = [
        _result(passed=True),
        _result(passed=False, category=EvalCategory.MULTI_STEP),
    ]
    report = aggregate(results)
    assert report.by_category["simple_faq"]["rate"] == 1.0
    assert report.by_category["multi_step"]["rate"] == 0.0


# ===== 转人工 precision/recall（T064，BUG-001 修复） =====


def test_human_metrics_all_correct() -> None:
    """3 条期望转人工全部转了、且无乱转：precision=recall=1.0。"""
    results = [
        _result(expect_human=True, need_human_intervention=True),
        _result(expect_human=True, need_human_intervention=True),
        _result(expect_human=True, need_human_intervention=True),
        _result(expect_human=False, need_human_intervention=False),
    ]
    report = aggregate(results)
    assert report.human_precision == 1.0
    assert report.human_recall == 1.0
    assert report.human_scored == 3
    assert report.human_intervened == 3


def test_human_metrics_all_missed() -> None:
    """2 条期望转人工全没转：recall=0；无实际转人工：precision=0（而非旧公式恒 1）。"""
    results = [
        _result(expect_human=True, need_human_intervention=False),
        _result(expect_human=True, need_human_intervention=False),
    ]
    report = aggregate(results)
    assert report.human_precision == 0.0
    assert report.human_recall == 0.0


def test_human_metrics_empty() -> None:
    """无任何转人工标注与行为：两项均 0.0（未考核），不再是退化值。"""
    report = aggregate([_result(), _result()])
    assert report.human_precision == 0.0
    assert report.human_recall == 0.0
    assert report.human_scored == 0


def test_human_metrics_mixed() -> None:
    """混合：4 期望转 3 转 1 漏；另 1 条不该转却转了 → recall=3/4、precision=3/4。"""
    results = [
        _result(expect_human=True, need_human_intervention=True),
        _result(expect_human=True, need_human_intervention=True),
        _result(expect_human=True, need_human_intervention=True),
        _result(expect_human=True, need_human_intervention=False),  # 漏转
        _result(expect_human=False, need_human_intervention=True),  # 乱转
    ]
    report = aggregate(results)
    assert report.human_precision == 3 / 4
    assert report.human_recall == 3 / 4
    assert report.human_intervened == 4


def test_score_case_transfers_expect_human() -> None:
    """score_case 把用例期望值透传到 result（聚合分母来源）。"""
    case = _case(must_include=["抱歉"], expect_human_intervention=True)
    r = score_case(case, _result(answer="抱歉，该问题需转人工处理"))
    assert r.expect_human is True
    r2 = score_case(_case(must_include=["好的"]), _result(answer="好的"))
    assert r2.expect_human is False


# ===== 意图准确率（T066，BUG-002） =====


def test_intent_match_scored() -> None:
    """标注了期望意图 → intent_match 有值（匹配/不匹配），计入分母。"""
    case = _case(must_include=["ok"], expected_intent="complex_consult")
    r = score_case(case, _result(answer="ok", actual_intent="complex_consult"))
    assert r.intent_match is True
    r2 = score_case(case, _result(answer="ok", actual_intent="single_domain"))
    assert r2.intent_match is False


def test_intent_unannotated_not_scored() -> None:
    """未标注期望意图 → intent_match 保持 None，聚合分母不计（不误增分母）。"""
    r = score_case(_case(), _result(answer="ok", actual_intent="simple_faq"))
    assert r.intent_match is None


def test_intent_accuracy_aggregation() -> None:
    """2/3 标注用例识别一致 → 准确率 2/3；未标注用例不进分母。"""
    results = [
        _result(intent_match=True),
        _result(intent_match=True),
        _result(intent_match=False),
        _result(intent_match=None),  # 未标注
    ]
    report = aggregate(results)
    assert report.intent_accuracy == 2 / 3
    assert report.intent_scored == 3


def test_intent_accuracy_empty_returns_none() -> None:
    """无标注用例 → intent_accuracy=None（未考核），而非 1.0 误导。"""
    report = aggregate([_result(), _result()])
    assert report.intent_accuracy is None
    assert report.intent_scored == 0


def test_result_from_a06_captures_intent() -> None:
    """适配层从 A06/state 捕获实际意图（识别结果缺失保持 None）。"""
    a06 = {"answer": "ok", "intent": "complex_consult"}
    r = result_from_a06(_case(must_include=["ok"]), a06, duration_s=0.1)
    assert r.actual_intent == "complex_consult"
    r2 = result_from_a06(_case(must_include=["ok"]), {"answer": "ok"}, duration_s=0.1)
    assert r2.actual_intent is None


# ===== result_from_a06 适配层 =====


def test_result_from_a06() -> None:
    case = _case(must_include=["张伟"], expected_tools=["policy_query"])
    a06 = {
        "answer": "张伟的保单保额 100 万",
        "used_tools": [{"tool": "policy_query", "input": {}, "output": {}}],
        "compliance_status": "PASS",
        "need_human_intervention": False,
    }
    r = result_from_a06(case, a06, duration_s=1.234)
    assert r.answer == a06["answer"]
    assert r.used_tools == ["policy_query"]
    assert r.compliance_status == "PASS"
    assert r.duration_s == 1.234
