"""理赔类工具装配：import 即注册到默认注册中心（应用入口 import tools.claim 完成装配）。"""

from tools.claim.policy_query import PolicyQueryInput, PolicyQueryOutput, PolicyQueryTool
from tools.registry import get_default_registry

get_default_registry().register(PolicyQueryTool())

__all__ = ["PolicyQueryTool", "PolicyQueryInput", "PolicyQueryOutput"]
