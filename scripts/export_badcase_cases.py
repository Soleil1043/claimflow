"""T174 badcase 回流 CLI（D080）：扫描 → 人工 triage → 并入回归集。

用法：
    uv run python -m scripts.export_badcase_cases               # 扫描改判案 → 候选文件
    # 人工 triage：编辑 regression_candidates.json，认可回流的条目 "status" 改 "adopt"
    #（不认可改 "drop"；triage_note 记理由）。重扫不吞未消费的 triage 决策
    uv run python -m scripts.export_badcase_cases --commit      # adopt 条目并入回归集（幂等）
    uv run python -m scripts.export_badcase_cases --list        # 候选与 triage 状态概览

评测（回归集可跑，暂不进 push CI——内容依赖 triage 积累）：
    uv run python -m evals.adjudication_suite --dataset regression
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.badcase_export import commit_adopted, scan_badcases  # noqa: E402

CANDIDATES_PATH = ROOT / "evals" / "datasets" / "regression_candidates.json"
REGRESSION_PATH = ROOT / "evals" / "datasets" / "adjudication_regression.json"


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"written: {path.relative_to(ROOT)}")


async def cmd_scan() -> int:
    """扫描改判案 → 候选文件（合并旧 triage 状态）。"""
    from evals.badcase_export import merge_triage
    from services.db.session import get_session_factory

    async with get_session_factory()() as session:
        candidates = await scan_badcases(session)

    prev = _load(CANDIDATES_PATH) or {}
    candidates = merge_triage(candidates, prev.get("candidates", []))

    pending = sum(1 for c in candidates if c.get("status") == "pending")
    adopted = sum(1 for c in candidates if c.get("status") == "adopt")
    data = {
        "_meta": {
            "task": "T174 badcase 回流候选（D080）",
            "guide": (
                "人工 triage：认可回流的条目把 status 改 adopt（triage_note 记理由），"
                "不认可改 drop。suggested_expected 默认 route=auto（修复后系统应得出"
                "坐席结论）；案件本就该人工裁量则改 route=human。replay 前核对 "
                "policy_no 是否在评测种子库。--commit 消费 adopt 条目"
            ),
            "count": len(candidates),
        },
        "candidates": candidates,
    }
    _write(CANDIDATES_PATH, data)
    print(f"改判案 {len(candidates)} 条（pending {pending} / 已 adopt {adopted}）")
    return 0


def cmd_commit() -> int:
    """adopt 候选 → 回归集（幂等）。"""
    cand_file = _load(CANDIDATES_PATH)
    if cand_file is None:
        print("错误：候选文件不存在（先跑 --scan）", file=sys.stderr)
        return 1
    dataset, added = commit_adopted(
        cand_file.get("candidates", []), _load(REGRESSION_PATH)
    )
    _write(REGRESSION_PATH, dataset)
    total = len(dataset["cases"])
    print(f"本次并入 {added} 条，回归集共 {total} 案")
    if total:
        print("评测：uv run python -m evals.adjudication_suite --dataset regression")
    return 0


def cmd_list() -> int:
    """候选概览（triage 状态与差异）。"""
    cand_file = _load(CANDIDATES_PATH)
    if cand_file is None:
        print("候选文件不存在（先跑 --scan）")
        return 0
    for c in cand_file.get("candidates", []):
        print(
            f"[{c.get('status')}] {c.get('source_case_id')} "
            f"{c.get('human', {}).get('operator')} 改判：{c.get('diff')}"
        )
    reg = _load(REGRESSION_PATH)
    if reg:
        print(f"回归集：{len(reg.get('cases', []))} 案")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="badcase 回流：坐席改判 → 回归集")
    parser.add_argument("--scan", action="store_true", help="扫描改判案生成候选（默认）")
    parser.add_argument("--commit", action="store_true", help="adopt 候选并入回归集")
    parser.add_argument("--list", action="store_true", help="候选概览")
    args = parser.parse_args()
    if args.commit:
        return cmd_commit()
    if args.list:
        return cmd_list()
    return asyncio.run(cmd_scan())


if __name__ == "__main__":
    sys.exit(main())
