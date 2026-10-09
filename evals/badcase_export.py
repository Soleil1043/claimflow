"""Badcase 回流导出（T174，D080）：坐席改判 → 评测候选 → 人工 triage → 独立回归集。

数据飞轮最小闭环（完善性评估建议 2）：
- 信号 = 决定书版本链 diff：auto 草稿（decision_generate，issued_by=auto）与最新
  坐席版（issued_by=agent:<id>）结论或金额变化。confirm（结论金额不变仅签发）
  不算 badcase——系统结论被认可，转人工是保守正确行为
- 期望值来源 = 坐席终局决议（非 LLM 自评）；conclusion→liability 映射
- triage 门 = 候选一律 status=pending，人工认可后改 adopt/drop（防坐席误操作/
  口径差异回流成脏标签）
- 防评估集污染 = 回流案进独立 adjudication_regression.json（REG-xxx，
  category=regression），不混入 153 金样本基线（D033 口径）
- replay 保真 = 材料内嵌原始 extraction（剥离 storage_path，评测走 _extract_one
  内嵌路径确定性复现当时材料事实）；保单依赖评测种子库（triage 时核对 policy_no）

CLI 见 scripts/export_badcase_cases.py；本模块只放可单测的纯逻辑与查询。
"""

from __future__ import annotations

import copy
import re
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.db.models import Case, CaseEvent, DecisionDocument

# 坐席终局 conclusion → 评测期望 liability（与 AdjudicationExpected 口径一致）
DECISION_TO_LIABILITY = {
    "approved": "covered",
    "rejected": "not_covered",
    "partial": "partial",
}

# 回归集类别名（与 injection/robustness 同级，gates 默认路径按主门口径判）
REGRESSION_CATEGORY = "regression"


def _amount_str(amount: Decimal | None) -> str:
    """金额统一字符串（None → 空串，展示用）。"""
    return str(amount) if amount is not None else ""


def _clean_materials(materials: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """案件材料 → 评测输入形态：剥离 storage_path（本地路径无重放意义），
    保留 file_name / doc_type / extraction（评测经 _extract_one 内嵌路径复现）。"""
    cleaned: list[dict[str, Any]] = []
    for entry in materials or []:
        if not isinstance(entry, dict):
            continue
        item = copy.deepcopy(entry)
        item.pop("storage_path", None)
        cleaned.append(item)
    return cleaned


def _diff_summary(
    auto_doc: DecisionDocument, agent_doc: DecisionDocument
) -> str:
    """改判差异一句话（triage 快速判读）。"""
    parts: list[str] = []
    if auto_doc.conclusion != agent_doc.conclusion:
        parts.append(f"结论 {auto_doc.conclusion}→{agent_doc.conclusion}")
    auto_amt = auto_doc.approved_amount or Decimal("0")
    agent_amt = agent_doc.approved_amount or Decimal("0")
    if auto_amt != agent_amt:
        parts.append(f"金额 {_amount_str(auto_amt)}→{_amount_str(agent_amt)}")
    return "；".join(parts)


def build_candidate(
    case: Case,
    auto_doc: DecisionDocument,
    agent_doc: DecisionDocument,
    observed_liability: str | None,
) -> dict[str, Any]:
    """改判案 → 候选条目（case 输入 + observed/human 对照 + 建议期望）。"""
    diff = _diff_summary(auto_doc, agent_doc)
    liability = DECISION_TO_LIABILITY.get(agent_doc.conclusion)
    suggested_amount = (
        str(agent_doc.approved_amount) if agent_doc.approved_amount is not None else "0.00"
    )
    return {
        "source_case_id": case.id,
        "status": "pending",  # pending / adopt / drop（人工 triage 修改）
        "triage_note": "",
        # 评测输入形态（AdjudicationCase 兼容；commit 时换 REG-xxx 编号）
        "case": {
            "case_id": case.id,
            "user_id": case.user_id,
            "policy_no": case.policy_no,
            "declared_case_type": case.case_type,
            "claimed_amount": str(case.claimed_amount),
            "incident_date": case.incident_date.isoformat(),
            "incident_description": case.incident_description,
            "materials": _clean_materials(case.materials),
        },
        # 自动管线当时的结论（重放目标：修复后系统应得出 human 的结论）
        "observed": {
            "decision": auto_doc.conclusion,
            "approved_amount": _amount_str(auto_doc.approved_amount),
            "liability": observed_liability,
            "issued_by": auto_doc.issued_by,
        },
        # 坐席终局决议（期望值唯一来源）
        "human": {
            "decision": agent_doc.conclusion,
            "approved_amount": _amount_str(agent_doc.approved_amount),
            "operator": agent_doc.issued_by.removeprefix("agent:"),
            "resolved_at": agent_doc.created_at.isoformat(),
        },
        "diff": diff,
        "suggested_expected": {
            "category": REGRESSION_CATEGORY,
            "route": "auto",  # 默认假设：修复后应自动得出坐席结论；triage 可改 human
            "liability": liability,
            "approved_amount": suggested_amount,
            "expected_worker_sequence": [],  # 回归案不钉序列（重派属实现细节）
            "note": f"回流（源 {case.id}）：坐席改判 {diff}；期望=坐席终局决议",
        },
    }


async def _observed_liability(session: AsyncSession, case_id: str) -> str | None:
    """自动管线当时的责任判定（首个 liability_judge 阶段结论；补件重跑取首次）。"""
    evt = (
        await session.execute(
            select(CaseEvent)
            .where(
                CaseEvent.case_id == case_id,
                CaseEvent.kind == "stage_result",
                CaseEvent.stage == "liability_judge",
            )
            .order_by(CaseEvent.seq.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if evt is None or not isinstance(evt.payload, dict):
        return None
    verdict = evt.payload.get("verdict")
    return str(verdict) if verdict else None


async def scan_badcases(session: AsyncSession) -> list[dict[str, Any]]:
    """扫描全部改判案（版本链 diff；confirm 与纯 auto 不捕）。"""
    rows = (
        await session.execute(
            select(Case, DecisionDocument)
            .join(DecisionDocument, DecisionDocument.case_id == Case.id)
            .order_by(Case.created_at, Case.id, DecisionDocument.version)
        )
    ).all()

    grouped: dict[str, tuple[Case, list[DecisionDocument]]] = {}
    for case, doc in rows:
        grouped.setdefault(case.id, (case, []))[1].append(doc)

    candidates: list[dict[str, Any]] = []
    for case_id, (case, docs) in grouped.items():
        auto_doc = next((d for d in docs if d.issued_by == "auto"), None)
        agent_doc = next(
            (d for d in reversed(docs) if d.issued_by.startswith("agent:")), None
        )
        if auto_doc is None or agent_doc is None:
            continue
        changed = (
            auto_doc.conclusion != agent_doc.conclusion
            or (auto_doc.approved_amount or Decimal("0"))
            != (agent_doc.approved_amount or Decimal("0"))
        )
        if not changed:
            continue
        candidates.append(
            build_candidate(
                case, auto_doc, agent_doc, await _observed_liability(session, case_id)
            )
        )
    return candidates


def merge_triage(
    candidates: list[dict[str, Any]], previous: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """重扫合并：同 source_case_id 保留旧 status / triage_note（不吞人工决策）。"""
    prev_by_source = {
        str(c.get("source_case_id")): c
        for c in previous
        if isinstance(c, dict) and c.get("source_case_id")
    }
    for cand in candidates:
        old = prev_by_source.get(str(cand["source_case_id"]))
        if old is not None:
            cand["status"] = str(old.get("status") or "pending")
            cand["triage_note"] = str(old.get("triage_note") or "")
    return candidates


def _next_reg_seq(existing_ids: list[str]) -> int:
    """回归集内最大 REG-序号 + 1。"""
    seqs = [
        int(m.group(1))
        for cid in existing_ids
        if (m := re.fullmatch(r"REG-(\d{3,})", str(cid)))
    ]
    return (max(seqs) + 1) if seqs else 1


def commit_adopted(
    candidates: list[dict[str, Any]], regression: dict[str, Any] | None
) -> tuple[dict[str, Any], int]:
    """triage=adopt 的候选并入回归集（幂等：_meta.provenance 按源案去重）。

    Returns: (回归集 dict, 本次并入数)
    """
    dataset = regression or {
        "_meta": {
            "task": "T174 badcase 回归集（D080：坐席改判回流，人工 triage 采纳）",
            "discipline": (
                "期望值=坐席终局决议（非 LLM 自评）；replay 依赖评测种子库中的"
                "保单/险种（triage 时核对 policy_no）；不混入 153 金样本基线"
            ),
            "provenance": {},
        },
        "cases": [],
        "frequency_signals": [],
    }
    provenance = dataset["_meta"].setdefault("provenance", {})
    known_sources = {str(v) for v in provenance.values()}

    cases = dataset["cases"]
    seq = _next_reg_seq([str(c.get("case_id") or "") for c in cases])
    added = 0
    for cand in candidates:
        source_id = str(cand.get("source_case_id"))
        if cand.get("status") != "adopt" or source_id in known_sources:
            continue
        reg_id = f"REG-{seq:03d}"
        seq += 1
        entry = copy.deepcopy(cand["case"])
        entry["case_id"] = reg_id
        expected = copy.deepcopy(cand["suggested_expected"])
        expected["note"] = str(expected.get("note") or "").replace(
            f"源 {source_id}", f"源 {source_id}（{reg_id}）"
        )
        entry["expected"] = expected
        cases.append(entry)
        provenance[reg_id] = source_id
        known_sources.add(source_id)
        added += 1
    return dataset, added
