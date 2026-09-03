"""风控筛查节点（F06）——T079 桩版（注入式名单查询；T083 规则引擎工具化）。

评分口径（T083 细化）：黑名单命中 → 90/high（高风险短路）；
90 天内已有 ≥2 次理赔 → 65/medium（继续流程，分级签发时转人工）；默认 5/low。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from schemas.stages import FraudCheckOutput
from services.case_store import CaseRecorder
from state import ClaimCaseState

FraudLookup = Callable[[str], Awaitable[dict[str, Any] | None]]


def make_fraud_check_node(recorder: CaseRecorder, fraud_lookup: FraudLookup):
    """风控节点工厂。fraud_lookup(user_id) -> {blacklisted?, recent_claims?, ...} | None。"""

    async def fraud_check_node(state: ClaimCaseState) -> dict[str, Any]:
        info = await fraud_lookup(state["user_id"]) or {}
        indicators: list[str] = []
        blacklisted = bool(info.get("blacklisted"))
        recent = int(info.get("recent_claims", 0))

        if blacklisted:
            score, level = 90.0, "high"
            indicators.append("blacklist_hit")
        elif recent >= 2:
            score, level = 65.0, "medium"
            indicators.append("high_frequency_claims")
        else:
            score, level = 5.0, "low"

        output = FraudCheckOutput(
            risk_score=score,
            risk_level=level,
            indicators=indicators,
            blacklisted=blacklisted,
            recent_claims_count=recent,
        ).model_dump(mode="json")

        await recorder.event(state["case_id"], "stage_result", stage="fraud_check", payload=output)
        return {"risk": output}

    return fraud_check_node
