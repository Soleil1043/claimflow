"""Mock 数据入库脚本（幂等，可重复执行）。

用法：
    uv run python -m scripts.seed          # 全部数据入库
    uv run python -m scripts.seed --only policies
    uv run python -m scripts.seed --only medical_records

数据源：data/mock/*.json，入库后供各查询工具使用。
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
from services.db.models import MedicalRecord, Policy
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


async def main(targets: list[str]) -> None:
    configure_logging()
    # dev 直接建表；prod 依赖 alembic 已迁移
    await init_db()
    if "policies" in targets:
        await seed_policies()
    if "medical_records" in targets:
        await seed_medical_records()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mock 数据入库")
    parser.add_argument(
        "--only",
        choices=["policies", "medical_records"],
        default=None,
        help="只入库指定数据集（缺省全部）",
    )
    args = parser.parse_args()
    targets = [args.only] if args.only else ["policies", "medical_records"]
    asyncio.run(main(targets))
    sys.exit(0)
