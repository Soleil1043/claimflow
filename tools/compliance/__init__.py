"""合规类工具装配：import 即注册到默认注册中心（应用入口 import tools.compliance 完成装配）。"""

from tools.compliance.rule_check import (
    ComplianceRuleCheckTool,
    RuleCheckInput,
    RuleCheckOutput,
)
from tools.compliance.risk_scoring import (
    RiskScoringInput,
    RiskScoringOutput,
    RiskScoringTool,
)
from tools.registry import get_default_registry

get_default_registry().register(ComplianceRuleCheckTool())
get_default_registry().register(RiskScoringTool())

__all__ = [
    "ComplianceRuleCheckTool",
    "RuleCheckInput",
    "RuleCheckOutput",
    "RiskScoringTool",
    "RiskScoringInput",
    "RiskScoringOutput",
]
