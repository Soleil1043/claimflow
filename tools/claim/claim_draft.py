"""理赔申请预填链接工具（T133，D057-4：引导提交=预填跳转，不在对话内立案）。

把对话收集的险种/出险信息编码为门户表单页 query 参数，前端 CaseForm 预填
（T136 接线）；正式提交仍走表单页——校验与材料上传在表单完成，幂等链路
（B01 POST /cases）不绕过。入参不合法属业务失败（LLM 换参重试）。
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from urllib.parse import urlencode

from pydantic import Field

from schemas.lines import LINE_PACKS
from schemas.tools import ToolInput
from tools.base import ClaimflowTool


class ClaimDraftLinkInput(ToolInput):
    """预填链接入参：case_type 必填，其余按对话已知程度选填。"""

    case_type: str = Field(
        description="险种 line：medical（医疗）/ auto（车险）/ property（财产）/ accident（意外）",
        min_length=1,
    )
    incident_date: str | None = Field(
        default=None, description="出险日期 YYYY-MM-DD（对话中已知时填）"
    )
    claimed_amount: float | None = Field(
        default=None, description="申请理赔金额（元，对话中已知时填）"
    )
    description: str | None = Field(
        default=None, description="出险经过简述（对话中已知时填）"
    )


class ClaimDraftLinkTool(ClaimflowTool):
    name: str = "claim_draft_link"
    description: str = (
        "客户有理赔申请意向时，基于对话已收集的信息生成门户理赔表单的预填链接"
        "（险种/出险日期/金额/经过），引导客户点击链接前往表单页完成正式提交与材料上传。"
        "仅在客户明确表达申请意向后使用；金额或日期不明时留空，由客户在表单页自行填写。"
    )
    args_schema: type[ClaimDraftLinkInput] = ClaimDraftLinkInput

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """同步壳（langchain 1.x 要求实现 _run）：本项目全链路 async。"""
        raise NotImplementedError(f"{self.name} 仅支持异步调用（ainvoke）")

    async def _arun(
        self,
        *,
        case_type: str,
        incident_date: str | None = None,
        claimed_amount: float | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        line = case_type.strip()
        if line not in LINE_PACKS:
            return {
                "success": False,
                "error_message": f"未知险种 {line}；有效取值：{', '.join(sorted(LINE_PACKS))}",
            }
        params: list[tuple[str, str]] = [("case_type", line)]
        if incident_date is not None:
            try:
                dt.date.fromisoformat(incident_date.strip())
            except ValueError:
                return {
                    "success": False,
                    "error_message": f"日期格式应为 YYYY-MM-DD，收到 {incident_date}",
                }
            params.append(("incident_date", incident_date.strip()))
        if claimed_amount is not None:
            if claimed_amount <= 0:
                return {"success": False, "error_message": "金额必须为正数"}
            params.append(("claimed_amount", f"{claimed_amount:.2f}"))
        if description:
            params.append(("description", description.strip()))
        return {
            "success": True,
            "url": "/?" + urlencode(params),
            "note": "请把该链接原样告知客户，并说明点击后表单已预填、"
            "只需补全信息并上传材料即可正式提交。",
        }
