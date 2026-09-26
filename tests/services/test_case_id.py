"""案件号原子自增测试（T155，D071）：计数行 upsert-returning 取代读-判-写。

保护三件事：顺序递增、存量懒自播种（旧库免迁移种子）、跨"实例"（两连接池
同库并发）不撞号——T150 修的是单实例进程内锁，这里锁死后者的多实例形态。
"""

from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
from services.case_service import generate_case_id, new_case
from services.db.models import Base, Case, CaseIdCounter
from services.db.session import dispose_engine


@pytest.fixture()
async def id_db(tmp_path: Path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'id_test.db').as_posix()}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module.swap_engine(engine, factory)
    yield factory
    await dispose_engine()


async def _raw_case(case_id: str) -> Case:
    return new_case(
        case_id=case_id,
        user_id="u-id",
        policy_no="POL-2025-0001",
        claimed_amount=Decimal("1000.00"),
        incident_date=dt.date(2026, 8, 1),
        incident_description="案号测试案件",
        materials=[],
    )


async def test_sequential_increment(id_db) -> None:
    """顺序生成：同会话连续取号严格递增，计数行落库。"""
    async with id_db() as s:
        first = await generate_case_id(s)
        second = await generate_case_id(s)
        await s.commit()
    year = dt.date.today().year
    assert first == f"CASE-{year}-0001"
    assert second == f"CASE-{year}-0002"
    async with id_db() as s:
        row = (await s.execute(select(CaseIdCounter))).scalar_one()
        assert row.year == year and row.last_no == 2


async def test_lazy_seed_from_existing_cases(id_db) -> None:
    """存量懒自播种：旧库已有 CASE-YYYY-0007（计数行空）→ 首号 0008 不撞号。"""
    year = dt.date.today().year
    async with id_db() as s:
        s.add(await _raw_case(f"CASE-{year}-0007"))
        await s.commit()

    async with id_db() as s:
        no = await generate_case_id(s)
        await s.commit()
    assert no == f"CASE-{year}-0008"


async def test_rollback_returns_number_to_pool(id_db) -> None:
    """回滚即还号：取号后事务回滚，计数与案件同生共死——下次取同一号不冲突。"""
    async with id_db() as s:
        no = await generate_case_id(s)
        await s.rollback()
    async with id_db() as s:
        again = await generate_case_id(s)
        await s.commit()
    assert again == no  # 未使用的号被回收复用（无空洞浪费）


async def test_concurrent_generation_two_instances(id_db) -> None:
    """跨实例并发：两个独立连接池（= 两个实例）并发取号 → 全程无撞号。"""
    import sqlite3

    # 复用同一文件库开第二个引擎（独立连接池，模拟实例 B）
    url = str(id_db.kw["bind"].url)
    path = url.split("///")[-1]
    engine_b = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory_b = async_sessionmaker(engine_b, expire_on_commit=False)
    # 两连接池并发：SQLite 写串行化由默认 5s busy timeout 兜住
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.close()

    async def gen(factory, tag: str) -> str:
        async with factory() as s:
            no = await generate_case_id(s)
            s.add(await _raw_case(no))  # 真实提交案件，锁死取号有效性
            await s.commit()
            return no

    tasks = [gen(id_db, "a")] + [gen(factory_b, "b") for _ in range(4)]
    results = await asyncio.gather(*tasks)
    await engine_b.dispose()

    assert len(results) == 5 and len(set(results)) == 5, f"撞号：{sorted(results)}"
    assert all(r.startswith("CASE-") for r in results)
