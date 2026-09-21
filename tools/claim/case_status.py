"""客服案件进度查询工具（T133，D057）。

复用 case_service.case_progress_snapshot——与 B02 案件详情同口径的紧凑投影。
案件不存在属业务失败（正常返回 success=False，LLM 引导客户核对案件编号）。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from schemas.tools import ToolInput
from services.case_service import case_progress_snapshot
from tools.base import ClaimflowTool


class CaseStatusQueryInput(ToolInput):
    """案件进度查询入参。"""

    case_id: str = Field(description="案件编号，格式 CASE-YYYY-NNNN", min_length=1)


class CaseStatusQueryTool(ClaimflowTool):
    name: str = "case_status_query"
    description: str = (
        "查询客户理赔案件的实时进度：当前状态、核赔阶段、核定结论、是否需补件及缺什么材料。"
        "客户提供案件编号（CASE-YYYY-NNNN 格式）后使用；"
        "客户不知道编号时，引导其在门户「我的案件」页面查看，禁止猜测编号。"
    )
    args_schema: type[CaseStatusQueryInput] = CaseStatusQueryInput

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """同步壳（langchain 1.x 要求实现 _run）：本项目全链路 async。"""
        raise NotImplementedError(f"{self.name} 仅支持异步调用（ainvoke）")

    async def _arun(self, *, case_id: str) -> dict[str, Any]:
        snapshot = await case_progress_snapshot(case_id.strip())
        if snapshot is None:
            return {
                "success": False,
                "error_message": f"未找到案件 {case_id}，请与客户核对案件编号（CASE-YYYY-NNNN）",
            }
        return {"success": True, **snapshot}
