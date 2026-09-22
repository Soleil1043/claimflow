"""客服问答金样本判分纯函数与数据集装载（T146，D066）。

判分三层（全部确定性，无 LLM judge）：
- keyword_groups：组间 AND、组内任一命中（大小写不敏感）
- forbidden：任一出现即失败（红线漏放）
- escalation：终态 status 与 expect_escalation 一致

检索断言（分层观测，不阻塞）：search_kb top-k 的 source_file 与
expected_rag_sources 交集非空。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.schemas import SupportQACase, SupportQADataset

ROOT = Path(__file__).resolve().parent.parent
DATASET_PATH = ROOT / "evals" / "datasets" / "support_qa.json"


def load_support_dataset(path: Path | None = None) -> tuple[list[SupportQACase], dict[str, Any]]:
    """装载客服金样本（_meta + cases）。"""
    data_path = path or DATASET_PATH
    raw = json.loads(data_path.read_text(encoding="utf-8"))
    dataset = SupportQADataset.model_validate(raw)
    return dataset.cases, dataset.meta


def _contains(text: str, keyword: str) -> bool:
    """大小写不敏感子串匹配（英文状态词 received/Received 等价）。"""
    return keyword.lower() in text.lower()


def score_keywords(reply: str, groups: list[list[str]]) -> tuple[bool, list[int]]:
    """关键词组判分：组间 AND、组内任一命中。

    返回 (是否全过, 未命中组下标列表)。
    """
    missed: list[int] = []
    for i, group in enumerate(groups):
        if not any(_contains(reply, kw) for kw in group):
            missed.append(i)
    return not missed, missed


def score_forbidden(reply: str, forbidden: list[str]) -> list[str]:
    """禁止词判分：返回命中的禁止词（空列表 = 通过）。"""
    return [kw for kw in forbidden if _contains(reply, kw)]


def score_rag_sources(
    retrieved_sources: list[str], expected: list[str]
) -> bool:
    """检索断言：任一期望源命中即过（宽松口径，D066-2）。"""
    if not expected:
        return True  # 无期望 = 该案不做检索断言（True 不影响通过率分母）
    joined = " ".join(retrieved_sources)
    return any(exp in joined for exp in expected)


def score_support_case(
    case: SupportQACase,
    reply: str,
    final_status: str,
    retrieved_sources: list[str] | None = None,
) -> dict[str, Any]:
    """单案三层判分 + 检索观测。

    - passed：关键词组全过 AND 禁止词零命中 AND escalation 一致（硬门）
    - rag_hit：检索断言（分层观测，不并入 passed）
    """
    kw_ok, missed_groups = score_keywords(reply, case.expected_keyword_groups)
    forbidden_hit = score_forbidden(reply, case.forbidden_keywords)
    escalation_ok = (final_status == "escalated") == case.expect_escalation
    rag_hit = score_rag_sources(retrieved_sources or [], case.expected_rag_sources)

    checks = {
        "keywords": kw_ok,
        "forbidden": not forbidden_hit,
        "escalation": escalation_ok,
    }
    return {
        "case_id": case.case_id,
        "category": case.category,
        "question": case.question,
        "reply_head": (reply or "")[:200],
        "final_status": final_status,
        "checks": checks,
        "missed_groups": missed_groups,
        "forbidden_hits": forbidden_hit,
        "rag_expected": bool(case.expected_rag_sources),
        "rag_hit": rag_hit,
        "passed": all(checks.values()),
    }


def aggregate_support(results: list[dict[str, Any]]) -> dict[str, Any]:
    """聚合：答案通过率（硬门）+ 检索命中率（观测）+ 分类明细。"""
    total = len(results)
    if total == 0:
        return {"total": 0, "passed": 0, "answer_pass_rate": 0.0, "rag_total": 0,
                "rag_hit": 0, "rag_hit_rate": None, "by_category": {}, "failures": []}

    passed = sum(1 for r in results if r["passed"])
    rag_scope = [r for r in results if r.get("rag_expected")]
    rag_hit_n = sum(1 for r in rag_scope if r["rag_hit"])

    by_category: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = by_category.setdefault(r["category"], {"total": 0, "passed": 0})
        bucket["total"] += 1
        if r["passed"]:
            bucket["passed"] += 1

    return {
        "total": total,
        "passed": passed,
        "answer_pass_rate": round(passed / total, 4),
        "rag_total": len(rag_scope),
        "rag_hit": rag_hit_n,
        "rag_hit_rate": round(rag_hit_n / len(rag_scope), 4) if rag_scope else None,
        "by_category": by_category,
        "failures": [r for r in results if not r["passed"]],
    }
