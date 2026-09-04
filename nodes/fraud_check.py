"""风控筛查节点（F06，T083 真实化）：信号来自工具层，评分走 rules 纯函数。

信号来源（fraud_lookup 注入，默认实现接黑名单 mock JSON + claim_records 90 天频率）：
- blacklisted → 90/high（高风险，orchestrator 短路转人工）
- 近 90 天 ≥2 次申请 → 65/medium（流程继续，分级签发阶段转人工）
- 无信号 → 5/low
"""

from __future__ import annotations

from typing import Any

from schemas.stages import FraudCheckOutput
from services.case_store import CaseRecorder
from state import ClaimCaseState
from tools.fraud.rules import evaluate_fraud_rules

FraudLookup = Any  # async (state) -> {blacklisted?, recent_claims?, ...} | None


def make_fraud_check_node(recorder: CaseRecorder, fraud_lookup: FraudLookup):
    """风控节点工厂。fraud_lookup(state) -> 风控信号 dict | None。"""

    async def fraud_check_node(state: ClaimCaseState) -> dict[str, Any]:
        info = await fraud_lookup(state) or {}
        blacklisted = bool(info.get("blacklisted"))
        recent = int(info.get("recent_claims", 0) or 0)

        rules = evaluate_fraud_rules(blacklisted=blacklisted, recent_claims=recent)

        output = FraudCheckOutput(
            risk_score=rules["risk_score"],
            risk_level=rules["risk_level"],
            indicators=rules["indicators"],
            blacklisted=blacklisted,
            recent_claims_count=recent,
        ).model_dump(mode="json")

        await recorder.event(state["case_id"], "stage_result", stage="fraud_check", payload=output)
        return {"risk": output}

    return fraud_check_node
