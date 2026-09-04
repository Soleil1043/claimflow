"""欺诈规则评分（F06，T083）：纯函数 + 工具包装（D012 双形态惯例）。

评分表（与金样本 CASE-2026-0015/0016/0017 期望对齐）：
- 黑名单命中 → 90 / high（高风险，orchestrator 短路转人工）
- 近 90 天已有 ≥2 次理赔申请 → 65 / medium（流程继续，分级签发阶段转人工）
- 单次申请 / 无信号 → low（正常）
"""

from __future__ import annotations

from typing import Any

from pydantic import PrivateAttr

from schemas.tools import ToolInput
from tools.base import ClaimflowTool


def evaluate_fraud_rules(
    *,
    blacklisted: bool = False,
    recent_claims: int = 0,
    high_amount: bool = False,
) -> dict[str, Any]:
    """纯函数：风控信号 → 评分/等级/触发指标。

    level 口径：score ≥80 high（短路转人工）、≥40 medium（分级签发转人工）、其余 low。
    """
    score = 5.0
    indicators: list[str] = []
    if blacklisted:
        score = 90.0
        indicators.append("blacklist_hit")
    if recent_claims >= 2:
        score = max(score, 65.0)
        indicators.append("high_frequency_claims")
    if high_amount:
        score = max(score, 70.0)
        indicators.append("high_amount_pattern")
    level = "high" if score >= 80 else ("medium" if score >= 40 else "low")
    return {"risk_score": score, "risk_level": level, "indicators": indicators}


class FraudRulesInput(ToolInput):
    """欺诈规则评分入参。"""

    blacklisted: bool = False
    recent_claims: int = 0
    high_amount: bool = False


class FraudRulesTool(ClaimflowTool):
    name: str = "evaluate_fraud_rules"
    description: str = (
        "按欺诈规则集评分：输入黑名单命中、近 90 天理赔次数、高额异常标记，"
        "返回风险评分（0-100）、风险等级与触发指标。"
    )
    args_schema: type[FraudRulesInput] = FraudRulesInput

    _scorer = PrivateAttr(default=None)

    def __init__(self, scorer=None, **kwargs: Any) -> None:
        """可注入评分函数（测试用），缺省 evaluate_fraud_rules。"""
        super().__init__(**kwargs)
        self._scorer = scorer or evaluate_fraud_rules

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(f"{self.name} 仅支持异步调用（ainvoke）")

    async def _arun(
        self, blacklisted: bool = False, recent_claims: int = 0, high_amount: bool = False
    ) -> dict[str, Any]:
        return self._scorer(
            blacklisted=blacklisted, recent_claims=recent_claims, high_amount=high_amount
        )
