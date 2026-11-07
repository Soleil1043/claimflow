"""客服案件进度查询工具（T133，D057；T202/D088 属主断言）。

复用 case_service.case_progress_snapshot——与 B02 案件详情同口径的紧凑投影。

属主断言（T202，C2 修复）：只查**本会话已授权**的案件——门户在案件详情页凭
创建回执令牌调授权端点落 support_case_grants 行。会话 id 由客服 Agent 执行
作用域（services.support.context）提供，不交给 LLM；作用域外 fail-closed。
dev 开放态（staff_keys 未配置）与 REST 同口径放行。

"案件不存在"与"未授权"返回同一条业务失败（success=False）——不给枚举 oracle
（案件号顺序生成，可区分即可探测哪些编号存在）。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from schemas.tools import ToolInput
from services.case_service import case_progress_snapshot, portal_access_open
from services.support import store
from services.support.context import current_conversation_id
from tools.base import ClaimflowTool

_UNAVAILABLE_MESSAGE = (
    "未找到可查询的案件 {case_id}。请与客户核对案件编号（CASE-YYYY-NNNN）；"
    "若编号无误，请引导客户在门户「我的案件」打开该案件详情页，"
    "再从详情页的在线客服发起咨询（客服仅能查询客户在本会话授权的本人案件）"
)


class CaseStatusQueryInput(ToolInput):
    """案件进度查询入参。"""

    case_id: str = Field(description="案件编号，格式 CASE-YYYY-NNNN", min_length=1)


async def _query_authorized(case_id: str) -> bool:
    """本会话是否可查询该案件（dev 开放态放行；无会话作用域 fail-closed）。"""
    if portal_access_open():
        return True
    conversation_id = current_conversation_id()
    if conversation_id is None:
        return False
    return await store.case_granted(conversation_id, case_id)


class CaseStatusQueryTool(ClaimflowTool):
    name: str = "case_status_query"
    description: str = (
        "查询客户理赔案件的实时进度：当前状态、核赔阶段、核定结论、是否需补件及缺什么材料。"
        "客户提供案件编号（CASE-YYYY-NNNN 格式）后使用；仅能查询客户已在本会话授权的本人案件。"
        "客户不知道编号时，引导其在门户「我的案件」页面查看，禁止猜测编号。"
    )
    args_schema: type[CaseStatusQueryInput] = CaseStatusQueryInput

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """同步壳（langchain 1.x 要求实现 _run）：本项目全链路 async。"""
        raise NotImplementedError(f"{self.name} 仅支持异步调用（ainvoke）")

    async def _arun(self, *, case_id: str) -> dict[str, Any]:
        case_id = case_id.strip()
        # 属主断言先于读库：未授权与不存在走同一失败口径（无枚举 oracle）
        snapshot = (
            await case_progress_snapshot(case_id) if await _query_authorized(case_id) else None
        )
        if snapshot is None:
            return {
                "success": False,
                "error_message": _UNAVAILABLE_MESSAGE.format(case_id=case_id),
            }
        return {"success": True, **snapshot}
