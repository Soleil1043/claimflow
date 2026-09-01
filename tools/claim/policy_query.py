"""保单查询工具（F04）。

按保单号或身份证号查询保单详情（险种、保额、生效日期、免赔额等）。
数据来源：policies 表（scripts/seed.py 从 data/mock/policies.json 入库）。

失败语义（T007 确立，T044 迁官方工具基类后保持）：
- 保单不存在 / 未提供查询条件 → 返回含 success=False 的结果 dict（Agent 向用户解释）
- 数据库连接等系统故障 → 抛异常，交给守卫层（tools/guards.py）重试/熔断
"""

from __future__ import annotations

from typing import Any

from pydantic import PrivateAttr, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from schemas.tools import ToolInput
from services.db.models import Policy
from services.db.session import get_session_factory
from tools.base import ClaimflowTool


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


class PolicyQueryTool(ClaimflowTool):
    # 注：name/description 必须带类型注解——pydantic 要求子类覆盖父类字段时显式标注
    name: str = "policy_query"
    description: str = (
        "根据保单号或身份证号查询保单详情，返回险种、保额、免赔额、赔付比例、"
        "生效/到期日期与保单状态。用户询问'我的保单'、'能赔多少'、'保障范围'时使用。"
    )
    args_schema: type[PolicyQueryInput] = PolicyQueryInput

    _session_factory: async_sessionmaker[AsyncSession] | None = PrivateAttr(default=None)

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession] | None = None, **kwargs: Any
    ) -> None:
        """可注入会话工厂（测试用），缺省用全局工厂。"""
        super().__init__(**kwargs)
        self._session_factory = session_factory

    def _factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory or get_session_factory()

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """同步壳（langchain 1.x 要求实现 _run）：本项目全链路 async，同步路径不可用。"""
        raise NotImplementedError(f"{self.name} 仅支持异步调用（ainvoke）")

    async def _arun(
        self, *, policy_no: str | None = None, id_card: str | None = None
    ) -> dict[str, Any]:
        async with self._factory()() as session:
            stmt = select(Policy)
            if policy_no:
                stmt = stmt.where(Policy.policy_no == policy_no)
            else:
                stmt = stmt.where(Policy.holder_id_card == id_card)
            rows = (await session.execute(stmt)).scalars().all()

        if not rows:
            identifier = policy_no or id_card
            return {
                "success": False,
                "error_message": f"未找到保单（查询条件: {identifier}）",
            }

        # 按保单号查是唯一场景；按身份证可能命中多张，返回列表
        policies = [self._to_dict(p) for p in rows]
        if len(policies) == 1:
            return {"success": True, "policy": policies[0]}
        return {"success": True, "policies": policies}

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
