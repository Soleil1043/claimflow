"""tools/fraud 测试（T083）：规则评分纯函数 + 黑名单查询 + 理赔频率。"""

from __future__ import annotations

import datetime as dt
import json as jsonlib
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.db.models import Base, ClaimRecord, Policy
from tools.fraud.blacklist import query_blacklist_by_id
from tools.fraud.history import query_claims_history
from tools.fraud.rules import evaluate_fraud_rules

# ---------- 规则评分（金样本口径） ----------


def test_rules_blacklist_high() -> None:
    """CASE-2026-0015 口径：黑名单命中 → 90/high，高风险短路转人工。"""
    rules = evaluate_fraud_rules(blacklisted=True)
    assert rules == {
        "risk_score": 90.0,
        "risk_level": "high",
        "indicators": ["blacklist_hit"],
    }


def test_rules_high_frequency_medium() -> None:
    """CASE-2026-0016 口径：90 天内 2 次申请 → 65/medium（分级签发转人工）。"""
    rules = evaluate_fraud_rules(recent_claims=2)
    assert rules["risk_score"] == 65.0
    assert rules["risk_level"] == "medium"
    assert rules["indicators"] == ["high_frequency_claims"]


def test_rules_clean_low() -> None:
    rules = evaluate_fraud_rules()
    assert rules["risk_score"] == 5.0
    assert rules["risk_level"] == "low"
    assert rules["indicators"] == []


def test_rules_single_claim_still_low() -> None:
    assert evaluate_fraud_rules(recent_claims=1)["risk_level"] == "low"


def test_rules_blacklist_dominates() -> None:
    """黑名单与高频同时命中 → 取最高分 90/high。"""
    rules = evaluate_fraud_rules(blacklisted=True, recent_claims=3)
    assert rules["risk_score"] == 90.0
    assert set(rules["indicators"]) == {"blacklist_hit", "high_frequency_claims"}


# ---------- 黑名单查询（mock JSON） ----------


async def test_blacklist_hit(tmp_path) -> None:
    import json as jsonlib

    path = tmp_path / "bl.json"
    path.write_text(
        jsonlib.dumps(
            [{"id_card": "330105199210184460", "reason": "涉嫌虚假材料"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = query_blacklist_by_id("330105199210184460")
    assert result["blacklisted"] is True
    assert "虚假材料" in result["reason"]


async def test_blacklist_miss_and_missing_file(tmp_path, monkeypatch) -> None:
    import json as jsonlib

    path = tmp_path / "bl.json"
    path.write_text(jsonlib.dumps([]), encoding="utf-8")
    assert query_blacklist_by_id("x")["blacklisted"] is False
    # 名单文件缺失 → fail-open 视为无黑名单
    monkeypatch.setattr("tools.fraud.blacklist.BLACKLIST_PATH", tmp_path / "nope.json")
    assert query_blacklist_by_id("x")["blacklisted"] is False


async def test_blacklist_tool_wrapper(tmp_path) -> None:
    from tools.fraud.blacklist import QueryBlacklistTool

    path = tmp_path / "bl.json"
    path.write_text(
        jsonlib.dumps([{"id_card": "ID-1", "reason": "r"}]), encoding="utf-8"
    )
    tool = QueryBlacklistTool(blacklist_path=path)
    result = await tool.ainvoke({"id_card": "ID-1"})
    assert result["blacklisted"] is True


# ---------- 理赔频率（DB） ----------


@pytest.fixture()
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(
            Policy(
                policy_no="POL-2026-0007",
                holder_name="孙强",
                holder_id_card="330103198805126778",
                product_name="x",
                product_type="医疗险",
                coverage_amount=Decimal("300000.00"),
                deductible=Decimal("5000.00"),
                payout_ratio=Decimal("0.7000"),
                effective_date=dt.date(2026, 3, 1),
                expiry_date=dt.date(2027, 2, 28),
                status="active",
            )
        )
        now = dt.datetime.now()
        s.add_all(
            [
                ClaimRecord(
                    claim_no="CLM-A",
                    policy_no="POL-2026-0007",
                    status="approved",
                    applied_amount=Decimal("4800.00"),
                    approved_amount=Decimal("3800.00"),
                    submitted_at=now - dt.timedelta(days=10),
                    updated_at=now - dt.timedelta(days=10),
                ),
                ClaimRecord(
                    claim_no="CLM-B",
                    policy_no="POL-2026-0007",
                    status="rejected",
                    applied_amount=Decimal("5400.00"),
                    approved_amount=Decimal("0.00"),
                    submitted_at=now - dt.timedelta(days=30),
                    updated_at=now - dt.timedelta(days=30),
                ),
                # 窗口外的老申请不计入
                ClaimRecord(
                    claim_no="CLM-OLD",
                    policy_no="POL-2026-0007",
                    status="paid",
                    applied_amount=Decimal("1000.00"),
                    approved_amount=Decimal("700.00"),
                    submitted_at=now - dt.timedelta(days=120),
                    updated_at=now - dt.timedelta(days=120),
                ),
            ]
        )
        await s.commit()
    yield factory
    await engine.dispose()


async def test_history_counts_recent_including_rejected(db_factory) -> None:
    """频率口径=申请行为（含被拒）；窗口外不计。"""
    result = await query_claims_history(
        "330103198805126778", days=90, session_factory=db_factory
    )
    assert result["recent_claims"] == 2
    assert result["window_days"] == 90
    assert {c["claim_no"] for c in result["claims"]} == {"CLM-A", "CLM-B"}


async def test_history_unknown_person_zero(db_factory) -> None:
    result = await query_claims_history(
        "999999", days=90, session_factory=db_factory
    )
    assert result["recent_claims"] == 0
