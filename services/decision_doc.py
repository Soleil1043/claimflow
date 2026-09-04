"""决定书构建服务（F09/F10，T085）。

金额安全设计（D037）：正文骨架由**代码模板**渲染——结论/核定金额/案件编号等关键
数字从责任认定与理算的结构化数据注入；LLM 只撰写"核定依据"叙述段落（不含金额数字，
叙述对象是责任认定依据与条款），从结构上杜绝幻觉金额。

正文落库/出站前过三层合规（review_decision_document，纯函数）：
1. 金额一致性断言——正文"核定金额"与理算结果精确相等（不依赖 LLM）
2. 红线规则——tools.compliance.rule_check.check_text（违规承诺话术）
3. PII 脱敏——tools.compliance.sensitive_filter.mask_sensitive

三态映射：金额不一致 → MODIFY（revise 代码重渲染修复）；红线违规 → REJECT
（转人工，模板+代码叙述不应出现，出现即叙述失控）；其余 → PASS。
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from schemas.stages import DecisionDocOutput
from tools.compliance.rule_check import check_text
from tools.compliance.sensitive_filter import mask_sensitive

_CONCLUSION_LABELS = {
    "approved": "核准赔付",
    "rejected": "拒绝赔付",
    "partial": "部分赔付",
}

# 正文核定金额提取（金额一致性断言用）
_BODY_AMOUNT_RE = re.compile(r"核定金额：([0-9][0-9,]*(?:\.[0-9]+)?)\s*元")

_APPEAL_CLAUSE = (
    "如对本决定有异议，可在收到决定书之日起 30 日内申请复核，"
    "或依法向保险纠纷调解组织申请调解、向人民法院提起诉讼。"
)


def conclusion_for(verdict: str) -> str:
    """责任结论 → 决定书结论（referred 不是决定书结论——转人工无文书）。"""
    if verdict == "not_covered":
        return "rejected"
    if verdict == "partial":
        return "partial"
    return "approved"


def fallback_narrative(liability: dict[str, Any], calc: dict[str, Any]) -> str:
    """规则版核定依据叙述（LLM 不可用/修订重渲染时的确定性兜底）。"""
    clauses = "、".join(liability.get("clause_references") or []) or "条款库未命中"
    return (
        f"经审核，{liability.get('reason', '')}。"
        f"上述结论依据条款：{clauses}。"
    )


def render_decision_document(
    *,
    case_id: str,
    case_type: str,
    liability: dict[str, Any],
    calc: dict[str, Any],
    material: dict[str, Any] | None = None,
    narrative: str | None = None,
    version: int = 1,
    issued_by: str = "auto",
) -> dict[str, Any]:
    """渲染决定书（代码模板 + 叙述段落），正文脱敏后返回 DecisionDocOutput dump。

    narrative：LLM 撰写的核定依据叙述（不含金额）；None/空 → 规则版兜底叙述。
    """
    verdict = str(liability.get("verdict", "covered"))
    conclusion = conclusion_for(verdict)
    approved = Decimal(str(calc.get("approved_amount") or "0"))
    if conclusion == "rejected":
        approved = Decimal("0.00")

    clauses = "、".join(liability.get("clause_references") or []) or "—"
    deductions = (
        "；".join(
            f"{d.get('name')} {d.get('amount')} 元"
            for d in calc.get("deductions", [])
            if isinstance(d, dict)
        )
        or "无"
    )
    narrative_text = (narrative or "").strip() or fallback_narrative(liability, calc)

    body = (
        f"案件编号：{case_id}（{case_type}险）\n"
        f"理赔结论：{_CONCLUSION_LABELS[conclusion]}\n"
        f"核定金额：{approved} 元\n"
        f"\n"
        f"责任认定：{liability.get('reason', '—')}\n"
        f"条款依据：{clauses}\n"
        f"核定依据：{narrative_text}\n"
        f"理算方式：{calc.get('calculation_basis', '—')}\n"
        f"扣减明细：{deductions}\n"
        f"\n{_APPEAL_CLAUSE}"
    )
    body = mask_sensitive(body)  # PII 脱敏（F10 第四层）

    doc = DecisionDocOutput(
        title=f"理赔决定书（{case_id}）",
        body=body,
        conclusion=conclusion,  # type: ignore[arg-type]
        approved_amount=approved,
        version=version,
    )
    return doc.model_dump(mode="json") | {"issued_by": issued_by}


def review_decision_document(
    decision: dict[str, Any], calc: dict[str, Any]
) -> dict[str, Any]:
    """三层合规审查（纯函数，F10）→ {verdict, violations, amount_consistent, suggestion}。

    - 金额一致性：正文核定金额与 decision.approved_amount、calc.approved_amount
      三方精确相等（Decimal 口径）；不一致 → MODIFY（代码重渲染可修复）
    - 红线规则：check_text 违规承诺话术 → REJECT（对外文书零容忍，转人工）
    """
    body = str(decision.get("body", ""))
    violations = [
        {"type": v.get("type"), "detail": v.get("detail")}
        for v in check_text(body)
    ]

    calc_amount = Decimal(str(calc.get("approved_amount") or "0"))
    doc_amount = Decimal(str(decision.get("approved_amount") or "0"))
    amount_consistent = doc_amount == calc_amount
    match = _BODY_AMOUNT_RE.search(body)
    if match is not None:
        body_amount = Decimal(match.group(1).replace(",", ""))
        amount_consistent = amount_consistent and body_amount == calc_amount
    if not amount_consistent:
        violations.append({"type": "amount_mismatch", "detail": "决定书金额与理算结果不一致"})

    if violations:
        # 红线话术 → REJECT；金额类 → MODIFY（代码重渲染可修复）
        has_red_line = any(v["type"] != "amount_mismatch" for v in violations)
        verdict = "REJECT" if has_red_line else "MODIFY"
    else:
        verdict = "PASS"

    return {
        "verdict": verdict,
        "violations": violations,
        "amount_consistent": amount_consistent,
        "risk_score": 0.0,
        "suggestion": violations[0]["detail"] if violations else None,
        "round": 1,
    }
