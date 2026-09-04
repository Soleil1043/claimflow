"""Mock 数据入库脚本（幂等，可重复执行）。

用法：
    uv run python -m scripts.seed          # 全部数据入库
    uv run python -m scripts.seed --only policies
    uv run python -m scripts.seed --only medical_records
    uv run python -m scripts.seed --only cases

数据源：data/mock/*.json，入库后供各查询工具/核赔主图使用。
cases 的 expected 块（金样本期望）不入库——由评测器（T088）/主图测试（T079）直接读 JSON。
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.core.logging import configure_logging, get_logger
from services.db.models import Case, ClaimRecord, MedicalRecord, Policy
from services.db.session import get_session_factory, init_db

log = get_logger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "mock"


def _load_policies() -> list[Policy]:
    """从 JSON 构造 Policy ORM 对象列表。"""
    raw = json.loads((DATA_DIR / "policies.json").read_text(encoding="utf-8"))
    policies = []
    for item in raw:
        policies.append(
            Policy(
                policy_no=item["policy_no"],
                holder_name=item["holder_name"],
                holder_id_card=item["holder_id_card"],
                product_name=item["product_name"],
                product_type=item["product_type"],
                coverage_amount=Decimal(item["coverage_amount"]),
                deductible=Decimal(item["deductible"]),
                payout_ratio=Decimal(item["payout_ratio"]),
                effective_date=dt.date.fromisoformat(item["effective_date"]),
                expiry_date=dt.date.fromisoformat(item["expiry_date"]),
                status=item["status"],
            )
        )
    return policies


async def seed_policies() -> int:
    """保单入库（幂等 upsert：按 policy_no 存在则更新，不存在则插入）。"""
    policies = _load_policies()
    factory = get_session_factory()
    inserted, updated = 0, 0
    async with factory() as session:
        existing = {
            p.policy_no: p
            for p in (await session.execute(select(Policy))).scalars().all()
        }
        for policy in policies:
            old = existing.get(policy.policy_no)
            if old is None:
                session.add(policy)
                inserted += 1
            else:
                # 全字段刷新（演示数据以 JSON 为准）
                for col in (
                    "holder_name",
                    "holder_id_card",
                    "product_name",
                    "product_type",
                    "coverage_amount",
                    "deductible",
                    "payout_ratio",
                    "effective_date",
                    "expiry_date",
                    "status",
                ):
                    setattr(old, col, getattr(policy, col))
                updated += 1
        await session.commit()
    log.info("seed_policies_done", inserted=inserted, updated=updated)
    return inserted + updated


async def seed_medical_records() -> int:
    """就诊记录入库（幂等 upsert：按身份证+就诊日期+诊断组合判重）。"""
    raw = json.loads((DATA_DIR / "medical_records.json").read_text(encoding="utf-8"))
    factory = get_session_factory()
    inserted, updated = 0, 0
    async with factory() as session:
        existing = {
            (m.patient_id_card, m.visit_date, m.diagnosis_desc): m
            for m in (await session.execute(select(MedicalRecord))).scalars().all()
        }
        for item in raw:
            key = (item["patient_id_card"], dt.date.fromisoformat(item["visit_date"]), item["diagnosis_desc"])
            record = MedicalRecord(
                patient_id_card=item["patient_id_card"],
                hospital=item["hospital"],
                department=item["department"],
                diagnosis_desc=item["diagnosis_desc"],
                icd10_code=item["icd10_code"],
                visit_date=dt.date.fromisoformat(item["visit_date"]),
                treatment=item["treatment"],
                total_amount=Decimal(item["total_amount"]),
            )
            old = existing.get(key)
            if old is None:
                session.add(record)
                inserted += 1
            else:
                for col in ("hospital", "department", "icd10_code", "treatment", "total_amount"):
                    setattr(old, col, getattr(record, col))
                updated += 1
        await session.commit()
    log.info("seed_medical_records_done", inserted=inserted, updated=updated)
    return inserted + updated


def _load_cases() -> list[Case]:
    """从 JSON 构造 Case ORM 对象列表（expected 块跳过，不入库）。"""
    raw = json.loads((DATA_DIR / "cases.json").read_text(encoding="utf-8"))
    cases = []
    for item in raw["cases"]:
        cases.append(
            Case(
                id=item["case_id"],
                user_id=item["user_id"],
                policy_no=item["policy_no"],
                # case_type 留 unknown——险种分类是 intake 阶段（F01）的职责
                case_type="unknown",
                status="received",
                claimed_amount=Decimal(item["claimed_amount"]),
                incident_date=dt.date.fromisoformat(item["incident_date"]),
                incident_description=item["incident_description"],
                materials=item.get("materials") or [],
            )
        )
    return cases


async def seed_cases() -> int:
    """金样本案件入库（幂等 upsert：按 case_id 判重）。

    只刷新案件"事实"字段；status/case_type/final_decision/approved_amount 为运行时
    字段（intake/主图写），重跑种子不重置，避免破坏进行中的演示案件。
    """
    cases = _load_cases()
    factory = get_session_factory()
    inserted, updated = 0, 0
    async with factory() as session:
        existing = {
            c.id: c for c in (await session.execute(select(Case))).scalars().all()
        }
        for case in cases:
            old = existing.get(case.id)
            if old is None:
                session.add(case)
                inserted += 1
            else:
                for col in (
                    "user_id",
                    "policy_no",
                    "claimed_amount",
                    "incident_date",
                    "incident_description",
                    "materials",
                ):
                    setattr(old, col, getattr(case, col))
                updated += 1
        await session.commit()
    log.info("seed_cases_done", inserted=inserted, updated=updated)
    return inserted + updated


def _load_claim_records() -> list[ClaimRecord]:
    """从 JSON 构造 ClaimRecord ORM 对象列表（T083 风控频率数据源）。"""
    raw = json.loads((DATA_DIR / "claim_records.json").read_text(encoding="utf-8"))
    records = []
    for item in raw:
        submitted = dt.datetime.fromisoformat(item["submitted_at"])
        records.append(
            ClaimRecord(
                claim_no=item["claim_no"],
                policy_no=item["policy_no"],
                status=item["status"],
                applied_amount=Decimal(item["applied_amount"]),
                approved_amount=Decimal(item["approved_amount"]),
                submitted_at=submitted,
                updated_at=submitted,
            )
        )
    return records


async def seed_claim_records() -> int:
    """历史理赔记录入库（幂等 upsert：按 claim_no 判重，全字段刷新）。"""
    records = _load_claim_records()
    factory = get_session_factory()
    inserted, updated = 0, 0
    async with factory() as session:
        existing = {
            r.claim_no: r
            for r in (await session.execute(select(ClaimRecord))).scalars().all()
        }
        for record in records:
            old = existing.get(record.claim_no)
            if old is None:
                session.add(record)
                inserted += 1
            else:
                for col in ("policy_no", "status", "applied_amount",
                            "approved_amount", "submitted_at", "updated_at"):
                    setattr(old, col, getattr(record, col))
                updated += 1
        await session.commit()
    log.info("seed_claim_records_done", inserted=inserted, updated=updated)
    return inserted + updated


async def main(targets: list[str]) -> None:
    configure_logging()
    # dev 直接建表；prod 依赖 alembic 已迁移
    await init_db()
    if "policies" in targets:
        await seed_policies()
    if "medical_records" in targets:
        await seed_medical_records()
    if "claim_records" in targets:
        await seed_claim_records()
    if "cases" in targets:
        await seed_cases()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mock 数据入库")
    parser.add_argument(
        "--only",
        choices=["policies", "medical_records", "claim_records", "cases"],
        default=None,
        help="只入库指定数据集（缺省全部）",
    )
    args = parser.parse_args()
    targets = (
        [args.only]
        if args.only
        else ["policies", "medical_records", "claim_records", "cases"]
    )
    asyncio.run(main(targets))
    sys.exit(0)
