"""人工介入门 resolve_review 纯函数测试（T201/C1）：坐席决议值域不跨值域翻转。

C1 回归点：坐席决议 decision（决定书结论值域 approved/rejected/partial）此前被
直接喂给期望责任认定值域（covered/not_covered/partial）的 conclusion_for，
"rejected" 落入 else 被渲染成"核准赔付"——改判拒赔与重写既有拒赔书两条路径
都会翻转。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from nodes.human_gate import resolve_review
from services.decision_doc import render_decision_document

_LIABILITY = {
    "verdict": "covered",
    "reason": "骨折住院属意外医疗责任",
    "clause_references": ["第三条 保险责任"],
}
_CALC = {"approved_amount": "4640.00", "calculation_basis": "公式", "deductions": []}


def _state(decision: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"case_id": "CASE-T201", "case_type": "accident", "decision": decision}


def _existing_doc(verdict: str) -> dict[str, Any]:
    """管线产出的既有决定书（责任认定值域经 conclusion_for 换算的正常口径）。"""
    return render_decision_document(
        case_id="CASE-T201", case_type="accident",
        liability={**_LIABILITY, "verdict": verdict}, calc=_CALC,
    )


def test_rewrite_decision_rejected_renders_rejection() -> None:
    """坐席改判拒赔 → 拒赔决定书（结论 rejected、正文"拒绝赔付"、核定 0 元）。"""
    update = resolve_review(
        _state(_existing_doc("covered")),
        {"action": "rewrite", "decision": "rejected", "note": "既往症不予赔付",
         "resolved_by": "agent-01"},
    )
    doc = update["decision_document"]
    assert update["final_decision"] == "rejected"
    assert doc["conclusion"] == "rejected"
    assert "理赔结论：拒绝赔付" in doc["body"]
    assert "核准赔付" not in doc["body"]
    assert Decimal(str(doc["approved_amount"])) == Decimal("0.00")
    assert doc["issued_by"] == "agent:agent-01"
    assert doc["version"] == 2


def test_rewrite_without_decision_keeps_existing_rejection() -> None:
    """rewrite 不传 decision：回退既有拒赔书结论，不得翻转为核准赔付。"""
    update = resolve_review(
        _state(_existing_doc("not_covered")),
        {"action": "rewrite", "note": "补充拒赔理由说明", "resolved_by": "agent-02"},
    )
    assert update["final_decision"] == "rejected"
    assert update["decision_document"]["conclusion"] == "rejected"
    assert "理赔结论：拒绝赔付" in update["decision_document"]["body"]


def test_rewrite_partial_and_approved_unchanged() -> None:
    """partial / approved 改判按原值签发（未受值域修复影响）。"""
    for decision in ("partial", "approved"):
        update = resolve_review(
            _state(_existing_doc("covered")),
            {"action": "rewrite", "decision": decision, "approved_amount": "3000.00",
             "note": "改判", "resolved_by": "agent-03"},
        )
        assert update["final_decision"] == decision
        assert Decimal(str(update["decision_document"]["approved_amount"])) == Decimal("3000.00")


def test_confirm_without_existing_doc_rejected() -> None:
    """无既有决定书的 confirm（高风险短路案）按坐席拒赔决议渲染拒赔书。"""
    update = resolve_review(
        _state(None),
        {"action": "confirm", "decision": "rejected", "note": "人工核定拒赔",
         "resolved_by": "agent-04"},
    )
    assert update["final_decision"] == "rejected"
    assert update["decision_document"]["conclusion"] == "rejected"
    assert update["decision_document"]["issued_by"] == "agent:agent-04"


def test_confirm_existing_doc_issued_as_is() -> None:
    """有既有决定书的 confirm 原样签发其结论（既有拒赔书仍为拒赔）。"""
    update = resolve_review(
        _state(_existing_doc("not_covered")),
        {"action": "confirm", "note": "复核通过", "resolved_by": "agent-05"},
    )
    assert update["final_decision"] == "rejected"
    assert update["decision_document"]["issued_by"] == "agent:agent-05"


def test_red_line_note_blocks_issue() -> None:
    """坐席文本含红线话术 → 不签发，安全兜底 referred。"""
    update = resolve_review(
        _state(_existing_doc("covered")),
        {"action": "rewrite", "decision": "rejected",
         "body": "本公司保证赔付，百分百全额给付。", "resolved_by": "agent-06"},
    )
    assert update == {"final_decision": "referred"}


def test_render_explicit_conclusion_bypasses_verdict_mapping() -> None:
    """render_decision_document 显式 conclusion 优先于 verdict 换算。"""
    doc = render_decision_document(
        case_id="C", case_type="medical", liability=_LIABILITY, calc=_CALC,
        conclusion="rejected",
    )
    assert doc["conclusion"] == "rejected"
    assert Decimal(str(doc["approved_amount"])) == Decimal("0.00")
    # 缺省 None 仍走责任认定值域换算（自动管线口径不变）
    auto = render_decision_document(
        case_id="C", case_type="medical",
        liability={**_LIABILITY, "verdict": "not_covered"}, calc=_CALC,
    )
    assert auto["conclusion"] == "rejected"
