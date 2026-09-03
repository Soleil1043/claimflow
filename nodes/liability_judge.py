"""责任认定节点（F07）——T079 桩版（关键词规则；T084 换 create_agent ReAct + 条款 RAG）。

判定顺序：保单无效 → not_covered；等待期未过 → not_covered；
命中除外关键词 → not_covered；识别自费/乙类自付金额 → partial（回填 self_pay_amount）；
否则 → covered。条款引用为占位（T084 起由 RAG 检索真实条款）。
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from schemas.stages import LiabilityOutput
from services.case_store import CaseRecorder
from state import ClaimCaseState

# 除外责任关键词 → 除外项名称（医疗险 pack；新险种 pack 扩展）
EXCLUSION_RULES: tuple[tuple[str, str], ...] = (
    ("整形", "整形美容"),
    ("美容", "整形美容"),
    ("种植牙", "牙科"),
    ("正畸", "牙科"),
    ("牙科", "牙科"),
    ("矫正", "矫正"),
)
# 自费/乙类自付金额识别（如"自费内固定材料2500元"）
_SELF_PAY_RE = re.compile(r"(?:自费|自付)[^0-9]{0,12}([0-9]+(?:\.[0-9]+)?)\s*元")

_EXCLUSION_CLAUSE = "第五条 责任免除"
_COVERED_CLAUSE = "第三条 保险责任"


def make_liability_judge_node(recorder: CaseRecorder):
    """责任认定节点工厂。"""

    async def liability_judge_node(state: ClaimCaseState) -> dict[str, Any]:
        description = state.get("incident_description", "")
        policy_out = state.get("policy") or {}

        verdict = "covered"
        reason = "出险事件属于保险责任范围"
        exclusions: list[str] = []
        self_pay: Decimal | None = None
        clauses = [_COVERED_CLAUSE]

        if not policy_out.get("coverage_valid", False):
            verdict = "not_covered"
            reason = str(policy_out.get("invalid_reason") or "保单保障无效")
            clauses = [_EXCLUSION_CLAUSE]
        elif policy_out.get("waiting_period_passed") is False:
            verdict = "not_covered"
            reason = "出险日在等待期内，属于责任免除范围"
            clauses = [_EXCLUSION_CLAUSE]
        else:
            for keyword, exclusion in EXCLUSION_RULES:
                if keyword in description:
                    verdict = "not_covered"
                    reason = f"「{exclusion}」属于责任免除范围"
                    exclusions.append(exclusion)
                    clauses = [_EXCLUSION_CLAUSE]
                    break

        if verdict == "covered":
            match = _SELF_PAY_RE.search(description)
            if match is not None:
                self_pay = Decimal(match.group(1))
                verdict = "partial"
                reason = f"属于保险责任范围，但含自费/乙类自付项目 {self_pay} 元需扣除"

        output = LiabilityOutput(
            verdict=verdict,  # type: ignore[arg-type]
            reason=reason,
            clause_references=clauses,
            exclusions_triggered=exclusions,
            self_pay_amount=self_pay,
            confidence=1.0,
        ).model_dump(mode="json")

        await recorder.event(
            state["case_id"], "stage_result", stage="liability_judge", payload=output
        )
        return {"liability": output}

    return liability_judge_node
