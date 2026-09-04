"""保单核验节点（F05，T083 真实化：条款要素由工具层提供）。

按保单要素核验：存在性 / 有效期 / 等待期（天数来自条款要素）/ 除外 / 限额，
并回填理算三要素（保额/免赔额/赔付比例）。等待期天数与除外清单不再硬编码在节点——
由 tools/claim/policy_query.get_policy_terms 按产品类型提供（新险种 pack 扩展点）。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from schemas.stages import PolicyVerifyOutput
from services.case_store import CaseRecorder
from state import ClaimCaseState

# 默认等待期（条款要素缺失时的兜底，与条款库口径一致）
DEFAULT_WAITING_PERIOD_DAYS = 30

PolicyLookup = Any  # async (policy_no) -> dict | None


def make_policy_verify_node(recorder: CaseRecorder, policy_lookup: PolicyLookup):
    """保单核验节点工厂。policy_lookup(policy_no) -> 保单要素 dict（含 terms）| None。"""

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

        terms = info.get("terms") or {}
        waiting_days = int(terms.get("waiting_period_days") or DEFAULT_WAITING_PERIOD_DAYS)
        effective = dt.date.fromisoformat(str(info["effective_date"]))
        expiry = dt.date.fromisoformat(str(info["expiry_date"]))
        is_active = str(info.get("status", "")) == "active"
        in_term = effective <= incident_date <= expiry
        coverage_valid = is_active and in_term
        waiting_passed = incident_date >= effective + dt.timedelta(days=waiting_days)

        invalid_reason: str | None = None
        if not is_active:
            invalid_reason = f"保单状态为 {info.get('status')}，保障已终止"
        elif not in_term:
            invalid_reason = f"出险日不在保障期（{effective} ~ {expiry}）内"
        elif not waiting_passed:
            invalid_reason = f"出险日在等待期（{waiting_days} 天）内"

        output = PolicyVerifyOutput(
            policy_found=True,
            coverage_valid=coverage_valid,
            waiting_period_passed=waiting_passed,
            coverage_scope=list(terms.get("coverage_scope") or []),
            exclusions=list(terms.get("exclusions") or []),
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
