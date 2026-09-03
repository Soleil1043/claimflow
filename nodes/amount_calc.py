"""金额理算节点（F08）——确定性计算，无 LLM（T083 后仅扩展扣减规则）。

口径（与金样本 _meta 假设一致）：
- not_covered → 核定 0 元
- 否则：(申请额 − 自费/自付 − 免赔额) × 赔付比例，封顶保额，下限 0
全链路 Decimal，序列化 str。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from schemas.stages import AmountCalcOutput, DeductionItem
from services.case_store import CaseRecorder
from state import ClaimCaseState

_CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def make_amount_calc_node(recorder: CaseRecorder):
    """理算节点工厂。"""

    async def amount_calc_node(state: ClaimCaseState) -> dict[str, Any]:
        liability_out = state.get("liability") or {}
        policy_out = state.get("policy") or {}
        # 输入经 TypedDict schema 不做类型转换，金额可能是 str——统一收敛 Decimal
        claimed = Decimal(str(state["claimed_amount"]))

        if liability_out.get("verdict") == "not_covered":
            output = AmountCalcOutput(
                claimed_amount=claimed,
                approved_amount=Decimal("0.00"),
                deductions=[],
                calculation_basis="责任不成立，不予赔付，核定 0.00 元",
            ).model_dump(mode="json")
            await recorder.event(
                state["case_id"], "stage_result", stage="amount_calc", payload=output
            )
            return {"calc": output}

        self_pay = Decimal(str(liability_out.get("self_pay_amount") or "0"))
        deductible = Decimal(str(policy_out.get("deductible") or "0"))
        ratio = Decimal(str(policy_out.get("payout_ratio") or "0"))
        policy_amount = Decimal(str(policy_out.get("policy_amount") or "0"))

        deductions: list[DeductionItem] = []
        if self_pay > 0:
            deductions.append(
                DeductionItem(name="自费/乙类自付项目", amount=self_pay, reason="非医保全额结算部分")
            )
        if deductible > 0:
            deductions.append(DeductionItem(name="免赔额", amount=deductible, reason="年度免赔额"))

        base = claimed - self_pay - deductible
        raw = base * ratio
        capped = min(max(raw, Decimal(0)), policy_amount)
        approved = _money(capped)

        parts = [f"({claimed}"]
        if self_pay > 0:
            parts.append(f"-{self_pay}自费")
        if deductible > 0:
            parts.append(f"-{deductible}免赔")
        parts.append(f")*{ratio}={approved}")
        if raw > policy_amount:
            parts.append(f"（超保额封顶 {policy_amount}）")
        basis = "".join(parts)

        output = AmountCalcOutput(
            claimed_amount=claimed,
            approved_amount=approved,
            deductions=deductions,
            calculation_basis=basis,
        ).model_dump(mode="json")

        await recorder.event(
            state["case_id"], "stage_result", stage="amount_calc", payload=output
        )
        return {"calc": output}

    return amount_calc_node
