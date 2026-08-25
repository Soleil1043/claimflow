"""T027 评测指标计算测试：判分规则、聚合口径、运行器适配。"""

from __future__ import annotations

from evals.metrics import CaseResult, aggregate, result_from_a06, score_case
from evals.schemas import EvalCase, EvalCategory


def _case(**kwargs: object) -> EvalCase:
    """构造测试用例（默认只考核 must_include）。"""
    base = {"id": "T-001", "category": EvalCategory.SIMPLE_FAQ, "user_input": "测试输入",
            "must_include": ["4640"]}
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
