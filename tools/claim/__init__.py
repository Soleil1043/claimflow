"""理赔类工具装配：import 即注册到默认注册中心（应用入口 import tools.claim 完成装配）。"""

from tools.claim.calculator import (
    ClaimCalculatorInput,
    ClaimCalculatorOutput,
    ClaimCalculatorTool,
)
from tools.claim.claim_rule_rag import (
    ClaimRuleRagInput,
    ClaimRuleRagOutput,
    ClaimRuleRagTool,
)
from tools.claim.policy_query import PolicyQueryInput, PolicyQueryOutput, PolicyQueryTool
from tools.registry import get_default_registry

get_default_registry().register(PolicyQueryTool())
get_default_registry().register(ClaimCalculatorTool())
get_default_registry().register(ClaimRuleRagTool())

__all__ = [
    "PolicyQueryTool",
    "PolicyQueryInput",
    "PolicyQueryOutput",
    "ClaimCalculatorTool",
    "ClaimCalculatorInput",
    "ClaimCalculatorOutput",
    "ClaimRuleRagTool",
    "ClaimRuleRagInput",
    "ClaimRuleRagOutput",
]
