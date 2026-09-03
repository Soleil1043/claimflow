"""保单核验节点（F05）——T079 桩版（确定性规则，T083 工具化）。

按保单要素核验：存在性 / 有效期 / 等待期（30 天，与条款库口径一致），
并回填理算三要素（保额/免赔额/赔付比例）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

from schemas.stages import PolicyVerifyOutput
from services.case_store import CaseRecorder
from state import ClaimCaseState

# 一般住院医疗等待期（与 data/kb_docs 条款口径一致，_meta 假设）
WAITING_PERIOD_DAYS = 30

PolicyLookup = Callable[[str], Awaitable[dict[str, Any] | None]]


def make_policy_verify_node(recorder: CaseRecorder, policy_lookup: PolicyLookup):
    """保单核验节点工厂。policy_lookup(policy_no) -> 保单要素 dict | None。"""

    async def policy_verify_node(state: ClaimCaseState) -> dict[str, Any]:
        info = await policy_lookup(state["policy_id"])
        # TypedDict 输入不做类型转换，日期可能是 ISO 字符串——统一收敛 date
        raw_incident = state["incident_date"]
        incident_date = (
            dt.date.fromisoformat(str(raw_incident))
            if isinstance(raw_incident, str)
            else raw_incident
        )

        if info is None:
            output = PolicyVerifyOutput(
                policy_found=False,
                coverage_valid=False,
                invalid_reason="保单不存在",
            ).model_dump(mode="json")
            await recorder.event(
                state["case_id"], "stage_result", stage="policy_verify", payload=output
            )
            return {"policy": output}

        effective = dt.date.fromisoformat(str(info["effective_date"]))
        expiry = dt.date.fromisoformat(str(info["expiry_date"]))
        is_active = str(info.get("status", "")) == "active"
        in_term = effective <= incident_date <= expiry
        coverage_valid = is_active and in_term
        waiting_passed = incident_date >= effective + dt.timedelta(days=WAITING_PERIOD_DAYS)

        invalid_reason: str | None = None
        if not is_active:
            invalid_reason = f"保单状态为 {info.get('status')}，保障已终止"
        elif not in_term:
            invalid_reason = f"出险日不在保障期（{effective} ~ {expiry}）内"
        elif not waiting_passed:
            invalid_reason = f"出险日在等待期（{WAITING_PERIOD_DAYS} 天）内"

        output = PolicyVerifyOutput(
            policy_found=True,
            coverage_valid=coverage_valid,
            waiting_period_passed=waiting_passed,
            coverage_scope=["疾病住院医疗", "住院手术"],
            exclusions=["整形美容", "牙科", "矫正", "先天性疾病", "既往症"],
            policy_amount=Decimal(str(info["coverage_amount"])),
            deductible=Decimal(str(info["deductible"])),
            payout_ratio=Decimal(str(info["payout_ratio"])),
            invalid_reason=invalid_reason,
        ).model_dump(mode="json")

        await recorder.event(
            state["case_id"], "stage_result", stage="policy_verify", payload=output
        )
        return {"policy": output}

    return policy_verify_node
