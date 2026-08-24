"""全局异常体系。

错误处理只在系统边界（用户输入、外部 API 调用）进行（AGENTS.md 4.1 约定），
内部代码信任调用方传参正确。
"""


class ClaimAgentError(Exception):
    """项目异常基类。"""


class ConfigError(ClaimAgentError):
    """配置加载/校验失败。"""


class ToolExecutionError(ClaimAgentError):
    """工具执行失败（由 ToolExecutor 统一处理，T007 实现）。"""

    def __init__(self, tool_name: str, message: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"工具 {tool_name} 执行失败: {message}")


class LLMError(ClaimAgentError):
    """LLM 调用失败（超时、限流、响应异常）。"""


class ComplianceRejectedError(ClaimAgentError):
    """合规审查拦截（REJECT，转人工流程，T018 实现）。"""
