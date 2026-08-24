"""保单查询工具（F04）。

按保单号或身份证号查询保单详情（险种、保额、生效日期、免赔额等）。
数据来源：policies 表（scripts/seed.py 从 data/mock/policies.json 入库）。

业务约定（T007 确立的失败语义）：
- 保单不存在 / 未提供查询条件 → 返回 success=False 的 ToolOutput（Agent 向用户解释）
- 数据库连接等系统故障 → 抛异常，交给 ToolExecutor 重试/熔断
"""

from __future__ import annotations

from typing import Any

from pydantic import model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from schemas.tools import ToolInput, ToolOutput
from services.db.models import Policy
from services.db.session import get_session_factory
from tools.base import BaseTool


class PolicyQueryInput(ToolInput):
    """保单查询入参：policy_no 与 id_card 至少提供一个。"""

    policy_no: str | None = None
    id_card: str | None = None

    @model_validator(mode="after")
    def _require_identifier(self) -> PolicyQueryInput:
        if not self.policy_no and not self.id_card:
            msg = "policy_no 与 id_card 至少提供一个"
            raise ValueError(msg)
        return self


class PolicyQueryOutput(ToolOutput):
    """保单查询输出：data 内为保单详情（单个或列表）。"""


class PolicyQueryTool(BaseTool[PolicyQueryInput, PolicyQueryOutput]):
    name = "policy_query"
    description = (
        "根据保单号或身份证号查询保单详情，返回险种、保额、免赔额、赔付比例、"
        "生效/到期日期与保单状态。用户询问'我的保单'、'能赔多少'、'保障范围'时使用。"
    )
    input_schema = PolicyQueryInput
    output_schema = PolicyQueryOutput

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        """可注入会话工厂（测试用），缺省用全局工厂。"""
        self._session_factory = session_factory

    def _factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory or get_session_factory()

    async def _run(self, input_data: PolicyQueryInput) -> PolicyQueryOutput:
        async with self._factory()() as session:
            stmt = select(Policy)
            if input_data.policy_no:
                stmt = stmt.where(Policy.policy_no == input_data.policy_no)
            else:
                stmt = stmt.where(Policy.holder_id_card == input_data.id_card)
            rows = (await session.execute(stmt)).scalars().all()

        if not rows:
            identifier = input_data.policy_no or input_data.id_card
            return PolicyQueryOutput(
                success=False,
                error_message=f"未找到保单（查询条件: {identifier}）",
            )

        # 按保单号查是唯一场景；按身份证可能命中多张，返回列表
        policies = [self._to_dict(p) for p in rows]
        data: dict[str, Any] = (
            {"policy": policies[0]} if len(policies) == 1 else {"policies": policies}
        )
        return PolicyQueryOutput(success=True, data=data)

    @staticmethod
    def _to_dict(p: Policy) -> dict[str, Any]:
        """ORM → 输出 dict（Decimal 转 float 便于 JSON 序列化；DB 侧全程 Decimal 保持精度）。"""
        return {
            "policy_no": p.policy_no,
            "holder_name": p.holder_name,
            "holder_id_card": p.holder_id_card,
            "product_name": p.product_name,
            "product_type": p.product_type,
            "coverage_amount": float(p.coverage_amount),
            "deductible": float(p.deductible),
            "payout_ratio": float(p.payout_ratio),
            "effective_date": p.effective_date.isoformat(),
            "expiry_date": p.expiry_date.isoformat(),
            "status": p.status,
        }
