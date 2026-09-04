"""理赔频率查询工具（F06）：按持有人证件号统计近 N 天理赔申请次数。

数据源：claim_records 表 join policies（按 holder_id_card）。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import PrivateAttr
from sqlalchemy import func, select

from schemas.tools import ToolInput
from services.db.models import ClaimRecord, Policy
from services.db.session import get_session_factory
from tools.base import ClaimflowTool

FREQUENCY_WINDOW_DAYS = 90


async def query_claims_history(
    id_card: str, days: int = FREQUENCY_WINDOW_DAYS, session_factory=None
) -> dict[str, Any]:
    """按持有人证件号统计近 N 天理赔申请（含被拒——频率信号看申请行为而非结论）。"""
    factory = session_factory or get_session_factory()
    since = dt.datetime.now() - dt.timedelta(days=days)
    async with factory() as session:
        rows = (
            await session.execute(
                select(ClaimRecord.claim_no, ClaimRecord.policy_no,
                       ClaimRecord.status, ClaimRecord.submitted_at)
                .join(Policy, Policy.policy_no == ClaimRecord.policy_no)
                .where(Policy.holder_id_card == id_card, ClaimRecord.submitted_at >= since)
                .order_by(ClaimRecord.submitted_at.desc())
            )
        ).all()
    claims = [
        {
            "claim_no": r.claim_no,
            "policy_no": r.policy_no,
            "status": r.status,
            "submitted_at": r.submitted_at.isoformat(),
        }
        for r in rows
    ]
    return {"recent_claims": len(claims), "window_days": days, "claims": claims}


async def count_recent_claims(id_card: str, days: int = FREQUENCY_WINDOW_DAYS) -> int:
    """轻量计数版（节点风控信号用）。"""
    factory = get_session_factory()
    since = dt.datetime.now() - dt.timedelta(days=days)
    async with factory() as session:
        return (
            await session.execute(
                select(func.count(ClaimRecord.id))
                .join(Policy, Policy.policy_no == ClaimRecord.policy_no)
                .where(Policy.holder_id_card == id_card, ClaimRecord.submitted_at >= since)
            )
        ).scalar_one()


class ClaimsHistoryInput(ToolInput):
    """理赔频率查询入参。"""

    id_card: str
    days: int = FREQUENCY_WINDOW_DAYS


class ClaimsHistoryTool(ClaimflowTool):
    name: str = "query_claims_history"
    description: str = (
        "按持有人身份证号统计近 N 天的理赔申请次数与明细。风控筛查、"
        "理赔频率异常检测场景使用。"
    )
    args_schema: type[ClaimsHistoryInput] = ClaimsHistoryInput

    _session_factory: Any = PrivateAttr(default=None)

    def __init__(self, session_factory=None, **kwargs: Any) -> None:
        """可注入会话工厂（测试用），缺省全局工厂。"""
        super().__init__(**kwargs)
        self._session_factory = session_factory

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(f"{self.name} 仅支持异步调用（ainvoke）")

    async def _arun(self, id_card: str, days: int = FREQUENCY_WINDOW_DAYS) -> dict[str, Any]:
        return await query_claims_history(id_card, days=days, session_factory=self._session_factory())
