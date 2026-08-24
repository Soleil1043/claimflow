"""风险评分工具（T018，F10）。

基于规则检查结果与文本信号计算风险分（0-100）：
- 分值构成：PROMISE +15 / ABSOLUTE +10 / MISLEAD +20 / FRAUD_RISK +60 / PRIVACY +30（封顶 100）
- 风险等级：≥80 high（REJECT 区）/ ≥50 medium / 其余 low

纯函数 score_risk 供合规节点兜底使用（decisions.md D012）。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field

from schemas.tools import ToolInput, ToolOutput
from tools.base import BaseTool
from tools.compliance.rule_check import check_text

# 各违规类型的基础分值
_TYPE_SCORES: dict[str, int] = {
    "PROMISE": 15,
    "ABSOLUTE": 10,
    "MISLEAD": 20,
    "FRAUD_RISK": 60,
    "PRIVACY": 30,
}

# 文本层面的额外欺诈信号（规则检查之外的组合信号）
_EXTRA_SIGNALS: list[tuple[str, int]] = [
    (r"多张保单|重复投保|短期内.*(?:投保|出险)", 25),
    (r"发票金额.*(?:修改|调整|改)", 20),
]


def score_risk(text: str, violations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """纯函数：计算风险分与等级。

    Args:
        text: 待评分文本（额外欺诈信号检测）
        violations: 规则检查结果（缺省时内部执行 check_text）
    """
    if violations is None:
        violations = check_text(text)

    breakdown: dict[str, int] = {}
    score = 0
    for violation in violations:
        vtype = str(violation.get("type", ""))
        weight = _TYPE_SCORES.get(vtype, 10)
        score += weight
        breakdown[vtype] = breakdown.get(vtype, 0) + weight

    for pattern, points in _EXTRA_SIGNALS:
        if re.search(pattern, text):
            score += points
            breakdown["EXTRA_SIGNAL"] = breakdown.get("EXTRA_SIGNAL", 0) + points

    score = min(score, 100)
    level = "high" if score >= 80 else ("medium" if score >= 50 else "low")
    return {
        "risk_score": score,
        "risk_level": level,
        "breakdown": breakdown,
        "violation_count": len(violations),
    }


class RiskScoringInput(ToolInput):
    """风险评分入参。"""

    text: str = Field(description="待评分的回答文本", min_length=1)


class RiskScoringOutput(ToolOutput):
    """评分输出：data 含 risk_score / risk_level / breakdown。"""


class RiskScoringTool(BaseTool[RiskScoringInput, RiskScoringOutput]):
    name = "risk_scoring"
    description = (
        "对拟返回给用户的回答计算合规风险分（0-100）与等级（low/medium/high）："
        "综合承诺话术、绝对化用语、误导表述、欺诈风险、隐私泄露等信号加权。"
        "合规审查评估风险等级时使用。"
    )
    input_schema = RiskScoringInput
    output_schema = RiskScoringOutput

    async def _run(self, input_data: RiskScoringInput) -> RiskScoringOutput:
        result = score_risk(input_data.text)
        return RiskScoringOutput(success=True, data=result)
