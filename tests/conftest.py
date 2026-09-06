"""全局测试夹具。"""

from __future__ import annotations

import os

import pytest

# 测试完全离线：HuggingFace 模型一律用本地缓存，禁止联网校验/下载。
# 一旦有测试路径意外触达 RAG/Embedding 且缓存元数据校验走网络，会在无外网
# 环境下无限阻塞（T046 期间实测：意图 mock 缺口 → 意外进入 RAG → HF 挂起）。
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


@pytest.fixture(autouse=True)
def _reset_graph_caches():
    """隔离 react/worker 子图全局缓存（T047）。

    create_agent 子图编译一次即缓存（generator._react_agent / runner._worker_cache），
    各测试文件 patch 的 get_chat_model 不同——不重置会串用上一测试的假模型。
    """
    import services.worker_agent as worker_agent_module

    worker_agent_module._worker_cache.clear()
    yield
    worker_agent_module._worker_cache.clear()


@pytest.fixture(autouse=True)
def _memory_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认关闭长期记忆写路径（T034）。

    A06 场景测试中 REJECT 终态会 force 触发记忆写入，若不关闭会真实加载
    BGE-M3（约 2GB）并写 ./data/qdrant；tests/memory/ 的专项测试自行显式开启。

    注意 settings 双实例陷阱（见 progress.md T028/T029）：patch 打在
    long_term 模块实际引用的 settings 对象上，保证对消费方生效。
    """
    import services.memory.long_term as long_term_module

    monkeypatch.setattr(long_term_module.settings, "memory_enabled", False)


# ===== 案件 API 测试公共内核（T109，评审二候选 6） =====
# test_cases 与 test_case_interventions 两份夹具的同构段（~80%）：文件库 + 种子保单 +
# LLM 全关 + mock 提取 + 真实零 LLM 桩图挂 app.state + inline 派发器。
# 差异保留在各文件（interventions 的共享 InMemorySaver + 可重建图是"跨重启"测试意图）。


async def make_case_api_core(monkeypatch, tmp_path, *, db_name: str = "cases_test.db"):
    """建文件库 + 换库 + 种子两张保单 + 关 LLM + mock 材料提取。返回 factory。"""
    from datetime import date
    from decimal import Decimal

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import services.db.session as session_module
    from services.db.models import Base, Policy

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / db_name).as_posix()}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module.swap_engine(engine, factory)
    monkeypatch.setattr(session_module.settings, "llm_api_key", "sk-test")
    for flag in ("orchestrator_llm_enabled", "material_review_llm_enabled",
                 "liability_llm_enabled", "decision_writer_llm_enabled"):
        monkeypatch.setattr(session_module.settings, flag, False)

    async def seed():
        async with factory() as s:
            s.add_all([
                Policy(
                    policy_no="POL-2025-0001", holder_name="张伟",
                    holder_id_card="330106199203154817",
                    product_name="安心医疗保险（旗舰版）", product_type="医疗险",
                    coverage_amount=Decimal("1000000"), deductible=Decimal("10000"),
                    payout_ratio=Decimal("0.8"), effective_date=date(2025, 1, 1),
                    expiry_date=date(2026, 12, 31), status="active",
                ),
                Policy(
                    policy_no="POL-2023-0004", holder_name="陈静",
                    holder_id_card="330104199001013328",
                    product_name="出行无忧意外伤害保险", product_type="意外险",
                    coverage_amount=Decimal("200000"), deductible=Decimal("0"),
                    payout_ratio=Decimal("0.9"), effective_date=date(2023, 8, 15),
                    expiry_date=date(2026, 8, 14), status="active",
                ),
            ])
            await s.commit()

    return engine, factory, seed
