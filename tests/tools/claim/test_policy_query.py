"""policy_query 工具测试（F04 验收）。

用内存 SQLite + 真实 seed 数据（data/mock/policies.json）验证：
- 按保单号查询返回完整详情
- 不存在的保单号返回 success=False 结构化错误
- 按身份证查询（单个 / 多个命中）
- 入参校验（两个标识都缺）
- OpenAI 工具 schema 导出
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.db.models import Base, Policy
from tools.claim.policy_query import PolicyQueryTool


@pytest.fixture()
async def tool():
    """内存库 + seed 一组保单，返回注入了会话工厂的工具实例。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add_all(
            [
                Policy(
                    policy_no="POL-2025-0001",
                    holder_name="张伟",
                    holder_id_card="330106199203154817",
                    product_name="安心医疗保险（旗舰版）",
                    product_type="医疗险",
                    coverage_amount=Decimal("1000000.00"),
                    deductible=Decimal("10000.00"),
                    payout_ratio=Decimal("0.8000"),
                    effective_date=dt.date(2025, 1, 1),
                    expiry_date=dt.date(2026, 12, 31),
                    status="active",
                ),
                Policy(
                    policy_no="POL-2025-0002",
                    holder_name="李娜",
                    holder_id_card="330105198811072546",
                    product_name="康宁重大疾病保险（2025 版）",
                    product_type="重疾险",
                    coverage_amount=Decimal("500000.00"),
                    deductible=Decimal("0.00"),
                    payout_ratio=Decimal("1.0000"),
                    effective_date=dt.date(2025, 6, 1),
                    expiry_date=dt.date(2045, 5, 31),
                    status="active",
                ),
                Policy(
                    policy_no="POL-2024-0003",
                    holder_name="王强",
                    holder_id_card="330108199506309212",
                    product_name="无忧住院医疗保险（标准版）",
                    product_type="医疗险",
                    coverage_amount=Decimal("300000.00"),
                    deductible=Decimal("5000.00"),
                    payout_ratio=Decimal("0.7000"),
                    effective_date=dt.date(2024, 3, 1),
                    expiry_date=dt.date(2025, 2, 28),
                    status="expired",
                ),
                # 与 0001 同一身份证：验证多保单命中
                Policy(
                    policy_no="POL-2026-0009",
                    holder_name="张伟",
                    holder_id_card="330106199203154817",
                    product_name="出行无忧意外伤害保险",
                    product_type="意外险",
                    coverage_amount=Decimal("200000.00"),
                    deductible=Decimal("0.00"),
                    payout_ratio=Decimal("0.9000"),
                    effective_date=dt.date(2026, 1, 1),
                    expiry_date=dt.date(2026, 12, 31),
                    status="active",
                ),
            ]
        )
        await session.commit()

    yield PolicyQueryTool(session_factory=factory)
    await engine.dispose()


async def test_query_by_policy_no(tool: PolicyQueryTool) -> None:
    """按保单号查询：返回完整详情（F04 验收主路径）。"""
    result = await tool.execute({"policy_no": "POL-2025-0001"})
    assert result.success is True
    policy = result.data["policy"]
    assert policy["holder_name"] == "张伟"
    assert policy["product_type"] == "医疗险"
    assert policy["coverage_amount"] == 1000000.0
    assert policy["deductible"] == 10000.0
    assert policy["payout_ratio"] == 0.8
    assert policy["effective_date"] == "2025-01-01"
    assert policy["status"] == "active"


async def test_policy_not_found(tool: PolicyQueryTool) -> None:
    """不存在的保单号：success=False 结构化错误（不抛异常）。"""
    result = await tool.execute({"policy_no": "POL-9999-XXXX"})
    assert result.success is False
    assert "未找到保单" in (result.error_message or "")
    assert "POL-9999-XXXX" in (result.error_message or "")


async def test_query_by_id_card_single(tool: PolicyQueryTool) -> None:
    """按身份证查询单张保单。"""
    result = await tool.execute({"id_card": "330105198811072546"})
    assert result.success is True
    assert result.data["policy"]["policy_no"] == "POL-2025-0002"


async def test_query_by_id_card_multiple(tool: PolicyQueryTool) -> None:
    """按身份证查询命中多张保单：返回 policies 列表。"""
    result = await tool.execute({"id_card": "330106199203154817"})
    assert result.success is True
    numbers = {p["policy_no"] for p in result.data["policies"]}
    assert numbers == {"POL-2025-0001", "POL-2026-0009"}


async def test_input_requires_identifier(tool: PolicyQueryTool) -> None:
    """入参校验：policy_no 与 id_card 都缺 → success=False。"""
    result = await tool.execute({})
    assert result.success is False
    assert "至少提供一个" in (result.error_message or "")


async def test_expired_policy_is_returned_with_status(tool: PolicyQueryTool) -> None:
    """过期保单正常返回（status=expired），由 Agent 结合状态解释。"""
    result = await tool.execute({"policy_no": "POL-2024-0003"})
    assert result.success is True
    assert result.data["policy"]["status"] == "expired"


def test_openai_tool_definition() -> None:
    """工具描述与参数 schema 符合 function calling 格式。"""
    definition = PolicyQueryTool().to_openai_tool()
    fn = definition["function"]
    assert fn["name"] == "policy_query"
    assert "保单" in fn["description"]
    props = fn["parameters"]["properties"]
    assert set(props) == {"policy_no", "id_card"}
