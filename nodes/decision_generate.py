"""决定书生成节点（F09）——T079 模板桩版（T085 换 LLM 撰写 + skill + 金额断言）。

按固定模板组装决定书草稿；结论与金额直接取自理算/责任结论（不经 LLM，杜绝幻觉金额）。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from schemas.stages import DecisionDocOutput
from services.case_store import CaseRecorder
from state import ClaimCaseState

_CONCLUSION_LABELS = {"approved": "核准赔付", "rejected": "拒绝赔付", "partial": "部分赔付"}


def make_decision_generate_node(recorder: CaseRecorder):
    """决定书生成节点工厂。"""

    async def decision_generate_node(state: ClaimCaseState) -> dict[str, Any]:
        liability_out = state.get("liability") or {}
        calc_out = state.get("calc") or {}

        verdict = str(liability_out.get("verdict", "covered"))
        if verdict == "not_covered":
            conclusion = "rejected"
        elif verdict == "partial":
            conclusion = "partial"
        else:
            conclusion = "approved"

        approved: Decimal = Decimal(str(calc_out.get("approved_amount") or "0"))
        clauses = "、".join(liability_out.get("clause_references", []) or ["—"])
        deductions = "；".join(
            f"{d.get('name')} {d.get('amount')} 元" for d in calc_out.get("deductions", [])
        ) or "无"

        body = (
            f"案件编号：{state['case_id']}\n"
            f"理赔结论：{_CONCLUSION_LABELS[conclusion]}\n"
            f"核定金额：{approved} 元\n"
            f"责任认定：{liability_out.get('reason', '—')}（条款依据：{clauses}）\n"
            f"理算方式：{calc_out.get('calculation_basis', '—')}\n"
            f"扣减明细：{deductions}\n"
            "\n如对本决定有异议，可在收到决定书之日起 30 日内申请复核。"
        )
        output = DecisionDocOutput(
            title=f"理赔决定书（{state['case_id']}）",
            body=body,
            conclusion=conclusion,  # type: ignore[arg-type]
            approved_amount=approved if conclusion != "rejected" else Decimal("0.00"),
            version=1,
        ).model_dump(mode="json")

        await recorder.event(
            state["case_id"], "stage_result", stage="decision_generate", payload=output
        )
        return {"decision": output}

    return decision_generate_node
