"""PII 库内加密测试（T176，D082）：加密语义 + ORM 透明读写 + 等值查询。

矩阵：
- 确定性：同明文恒同密文（等值可查）；不同明文密文不同
- v1: 前缀与长度（密文 > 明文）
- decrypt 容错：非前缀值原样返回（未迁移明文共存）；密钥不符 fail loud
- ORM 透明：写入加密 / 读取解密，种子与工具代码零改动
- raw SQL 读到密文（DB 泄漏不直接等于 PII 泄漏的验收口径）
- 等值查询：WHERE 证件号 = 明文 可命中（policy_query / fraud / record_query 依赖）
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.core.crypto as crypto_module
from app.core.crypto import decrypt_pii, encrypt_pii
from services.db.models import Base, MedicalRecord, Policy

ID_CARD = "330106199203154817"


class TestCryptoPrimitives:
    def test_roundtrip(self):
        stored = encrypt_pii(ID_CARD)
        assert stored.startswith("v1:")
        assert ID_CARD not in stored
        assert decrypt_pii(stored) == ID_CARD

    def test_deterministic(self):
        """同明文恒同密文——等值查询的前提。"""
        assert encrypt_pii(ID_CARD) == encrypt_pii(ID_CARD)

    def test_distinct_plaintexts_distinct_ciphertexts(self):
        assert encrypt_pii("330106199203154817") != encrypt_pii("330104199001013328")

    def test_ciphertext_longer_than_plaintext(self):
        assert len(encrypt_pii(ID_CARD)) > len(ID_CARD)

    def test_decrypt_passthrough_for_unmigrated_plaintext(self):
        """非 v1: 前缀原样返回——滚动迁移期明文行/手写值仍可读。"""
        assert decrypt_pii(ID_CARD) == ID_CARD
        assert decrypt_pii("普通文本") == "普通文本"

    def test_wrong_key_fails_loud(self, monkeypatch):
        """密钥不符 → ValueError（静默错读比读不到更糟）。"""
        stored = encrypt_pii(ID_CARD)
        monkeypatch.setattr(crypto_module.settings, "pii_encryption_key", "another-key")
        with pytest.raises(ValueError, match="解密失败"):
            decrypt_pii(stored)


class TestEncryptedColumn:
    async def test_orm_transparent_and_raw_sql_sees_ciphertext(self, tmp_path: Path):
        """ORM 写读透明；raw SQL（绕过 ORM 类型）读到 v1: 密文。"""
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{(tmp_path / 'pii_test.db').as_posix()}"
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async with factory() as s:
            s.add(
                Policy(
                    policy_no="POL-2026-0999",
                    holder_name="张伟",
                    holder_id_card=ID_CARD,
                    product_name="安心医疗保险（旗舰版）",
                    product_type="医疗险",
                    coverage_amount=Decimal("1000000"),
                    deductible=Decimal("10000"),
                    payout_ratio=Decimal("0.8"),
                    effective_date=dt.date(2025, 1, 1),
                    expiry_date=dt.date(2026, 12, 31),
                    status="active",
                )
            )
            await s.commit()

        # raw SQL：拖库视角只见密文
        async with factory() as s:
            raw = (
                await s.execute(
                    text("SELECT holder_id_card FROM policies WHERE policy_no = 'POL-2026-0999'")
                )
            ).scalar_one()
        assert raw.startswith("v1:")
        assert ID_CARD not in raw

        # ORM 读取：透明解密
        async with factory() as s:
            policy = (
                await s.execute(select(Policy).where(Policy.policy_no == "POL-2026-0999"))
            ).scalar_one()
            assert policy.holder_id_card == ID_CARD

            # 等值查询：明文入参 → 绑定参数加密 → 命中密文行
            hit = (
                await s.execute(select(Policy).where(Policy.holder_id_card == ID_CARD))
            ).scalar_one_or_none()
            assert hit is not None and hit.policy_no == "POL-2026-0999"

            # 不同证件号等值查询不误命中
            miss = (
                await s.execute(
                    select(Policy).where(Policy.holder_id_card == "330104199001013328")
                )
            ).scalar_one_or_none()
            assert miss is None

        await engine.dispose()

    async def test_medical_record_column_roundtrip(self, tmp_path: Path):
        """medical_records.patient_id_card 同口径（record_query 等值依赖）。"""
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{(tmp_path / 'pii_test2.db').as_posix()}"
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async with factory() as s:
            s.add(
                MedicalRecord(
                    patient_id_card=ID_CARD,
                    hospital="市一医院",
                    department="普外科",
                    diagnosis_desc="急性阑尾炎",
                    icd10_code="K35",
                    visit_date=dt.date(2026, 8, 12),
                    treatment="住院手术",
                    total_amount=Decimal("15800.00"),
                )
            )
            await s.commit()

        async with factory() as s:
            row = (
                await s.execute(
                    select(MedicalRecord).where(MedicalRecord.patient_id_card == ID_CARD)
                )
            ).scalar_one()
            assert row.diagnosis_desc == "急性阑尾炎"
        await engine.dispose()
