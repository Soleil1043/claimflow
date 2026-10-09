"""Badcase 回流导出单测（T174，D080）：信号筛选 / 候选构造 / triage 合并 / commit 幂等。

矩阵：
- 扫描只捕「auto 草稿 → agent 改判且结论或金额变化」；confirm（结论金额不变）与
  纯 auto_issued（无坐席版）不捕
- 候选含 observed（自动管线当时）/ human（坐席终局）/ diff / suggested_expected
  （期望=坐席终局决议映射）；材料剥离 storage_path（评测走内嵌 extraction 复现）
- merge_triage：重扫保留同源案的人工 status / triage_note
- commit_adopted：只并入 adopt；REG 编号续接；同源案幂等不重复
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from evals.badcase_export import (
    DECISION_TO_LIABILITY,
    commit_adopted,
    merge_triage,
    scan_badcases,
)
from services.db.models import Base, Case, CaseEvent, DecisionDocument


async def _mk_db(tmp_path: Path) -> async_sessionmaker:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'badcase_test.db').as_posix()}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def _case(case_id: str, *, materials: list | None = None) -> Case:
    return Case(
        id=case_id,
        user_id=f"u-{case_id.lower()}",
        policy_no="POL-2025-0001",
        case_type="medical",
        status="closed",
        claimed_amount=Decimal("15800.00"),
        incident_date=dt.date(2026, 8, 10),
        incident_description="急性阑尾炎住院手术，共花费15800元。",
        materials=materials
        or [
            {
                "file_name": "invoice.jpg",
                "doc_type": "invoice",
                "storage_path": "./data/uploads/x/invoice.jpg",
                "extraction": {
                    "patient_name": "张伟",
                    "diagnosis": "急性阑尾炎",
                    "amount": 15800.0,
                    "date": "2026-08-12",
                    "source": "vision",
                },
            }
        ],
    )


def _doc(
    case_id: str,
    version: int,
    conclusion: str,
    amount: Decimal | None,
    issued_by: str,
) -> DecisionDocument:
    return DecisionDocument(
        case_id=case_id,
        version=version,
        title="理赔决定书",
        body="正文",
        conclusion=conclusion,
        approved_amount=amount,
        issued_by=issued_by,
        created_at=dt.datetime(2026, 9, 1, 12, 0, 0),
    )


@pytest.fixture()
async def session(tmp_path: Path):
    """三案种子：A=改判（金额变）/ B=confirm（结论金额不变）/ C=纯 auto 单版。"""
    factory = await _mk_db(tmp_path)
    async with factory() as s:
        s.add(_case("CASE-2026-0001"))
        s.add_all([
            _doc("CASE-2026-0001", 1, "approved", Decimal("4640.00"), "auto"),
            _doc("CASE-2026-0001", 2, "approved", Decimal("3000.00"), "agent:alice"),
        ])
        s.add(_case("CASE-2026-0002"))
        s.add_all([
            _doc("CASE-2026-0002", 1, "approved", Decimal("4640.00"), "auto"),
            _doc("CASE-2026-0002", 2, "approved", Decimal("4640.00"), "agent:bob"),
        ])
        s.add(_case("CASE-2026-0003"))
        s.add(_doc("CASE-2026-0003", 1, "approved", Decimal("4640.00"), "auto"))
        # A 案自动管线的责任结论（observed.enrichment）
        s.add(
            CaseEvent(
                case_id="CASE-2026-0001",
                kind="stage_result",
                stage="liability_judge",
                seq=5,
                payload={"verdict": "covered"},
            )
        )
        await s.commit()
    return factory


class TestScan:
    async def test_only_rewrites_captured(self, session):
        async with session() as s:
            candidates = await scan_badcases(s)
        ids = [c["source_case_id"] for c in candidates]
        assert ids == ["CASE-2026-0001"]  # B（confirm）与 C（纯 auto）不捕

    async def test_candidate_fields(self, session):
        async with session() as s:
            cand = (await scan_badcases(s))[0]
        assert cand["status"] == "pending"
        assert cand["observed"] == {
            "decision": "approved",
            "approved_amount": "4640.00",
            "liability": "covered",
            "issued_by": "auto",
        }
        assert cand["human"]["decision"] == "approved"
        assert cand["human"]["approved_amount"] == "3000.00"
        assert cand["human"]["operator"] == "alice"
        assert "金额 4640.00→3000.00" in cand["diff"]
        # 期望值 = 坐席终局决议映射
        assert cand["suggested_expected"]["liability"] == "covered"
        assert cand["suggested_expected"]["approved_amount"] == "3000.00"
        assert cand["suggested_expected"]["route"] == "auto"
        assert cand["suggested_expected"]["category"] == "regression"
        # 材料剥离 storage_path、保留内嵌 extraction（评测确定性复现）
        mat = cand["case"]["materials"][0]
        assert "storage_path" not in mat
        assert mat["extraction"]["diagnosis"] == "急性阑尾炎"
        # 评测输入形态齐备（AdjudicationCase 兼容）
        for key in ("case_id", "user_id", "policy_no", "declared_case_type",
                    "claimed_amount", "incident_date", "incident_description"):
            assert cand["case"][key]

    async def test_conclusion_rewrite_also_captured(self, tmp_path):
        """结论改判（approved→rejected）同样入候选，liability 映射 not_covered。"""
        factory = await _mk_db(tmp_path)
        async with factory() as s:
            s.add(_case("CASE-2026-0009"))
            s.add_all([
                _doc("CASE-2026-0009", 1, "approved", Decimal("4640.00"), "auto"),
                _doc("CASE-2026-0009", 2, "rejected", Decimal("0.00"), "agent:carol"),
            ])
            await s.commit()
        async with factory() as s:
            cand = (await scan_badcases(s))[0]
        assert "结论 approved→rejected" in cand["diff"]
        assert cand["suggested_expected"]["liability"] == "not_covered"


class TestMergeTriage:
    def test_rescan_preserves_human_decisions(self):
        candidates = [
            {"source_case_id": "CASE-2026-0001", "status": "pending", "triage_note": ""},
            {"source_case_id": "CASE-2026-0002", "status": "pending", "triage_note": ""},
        ]
        previous = [
            {
                "source_case_id": "CASE-2026-0001",
                "status": "adopt",
                "triage_note": "坐席改判正确，回流",
            }
        ]
        merged = merge_triage(candidates, previous)
        assert merged[0]["status"] == "adopt"
        assert merged[0]["triage_note"] == "坐席改判正确，回流"
        assert merged[1]["status"] == "pending"


class TestCommitAdopted:
    def test_only_adopted_merged_and_idempotent(self):
        candidates = [
            {"source_case_id": "C1", "status": "adopt", "triage_note": "",
             "case": {"case_id": "C1", "user_id": "u1", "policy_no": "P1",
                      "declared_case_type": "medical", "claimed_amount": "15800.00",
                      "incident_date": "2026-08-10", "incident_description": "d",
                      "materials": []},
             "suggested_expected": {"category": "regression", "route": "auto",
                                    "liability": "covered", "approved_amount": "3000.00",
                                    "expected_worker_sequence": [], "note": "回流（源 C1）"}},
            {"source_case_id": "C2", "status": "drop", "triage_note": "坐席误操作",
             "case": {"case_id": "C2"}, "suggested_expected": {}},
        ]
        dataset, added = commit_adopted(candidates, None)
        assert added == 1
        assert [c["case_id"] for c in dataset["cases"]] == ["REG-001"]
        entry = dataset["cases"][0]
        assert entry["expected"]["approved_amount"] == "3000.00"
        assert dataset["_meta"]["provenance"] == {"REG-001": "C1"}

        # 幂等：同源案再 commit 不重复；新 adopt 案续接 REG-002
        candidates[1]["status"] = "adopt"
        candidates[1]["case"] = dict(candidates[0]["case"], case_id="C2")
        candidates[1]["suggested_expected"] = dict(
            candidates[0]["suggested_expected"], approved_amount="0.00"
        )
        dataset2, added2 = commit_adopted(candidates, dataset)
        assert added2 == 1
        assert [c["case_id"] for c in dataset2["cases"]] == ["REG-001", "REG-002"]
        assert dataset2["_meta"]["provenance"]["REG-001"] == "C1"


def test_decision_to_liability_mapping_complete():
    assert DECISION_TO_LIABILITY == {
        "approved": "covered",
        "rejected": "not_covered",
        "partial": "partial",
    }
