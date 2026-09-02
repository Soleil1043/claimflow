"""T067 一次性脚本：把 must_include 中的纯数字关键词迁移为 expected_numbers 精确断言。

迁移规则（D033）：
- 仅迁 must_include（AND → AND，语义不变、匹配升级为"数字归一化 + 边界断言"，
  640 不再混过 4640）；
- any_of 的纯数字项**不迁**：any_of 是 OR 同义容错（"80%" vs "1.0"），迁走会把
  容错变成 AND 必挂——模糊判分用例保持原语义（取舍记录于 D033/progress）
- 同值去重按归一化口径（"4,640" 与 "4640" 只留一个）

用法：uv run python scripts/annotate_numbers.py [--dry-run]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DATASETS = [
    Path("evals/datasets/eval_dataset.json"),
    Path("evals/datasets/eval_graph_assoc.json"),
]

# 纯数字项：数字（含全角/千分位/小数）可带 % 尾；不允许夹文字
_FULLWIDTH = "０１２３４５６７８９"
_NUMBER_ITEM = re.compile(rf"^[0-9{_FULLWIDTH}]+(?:[.,，．][0-9{_FULLWIDTH}]+)*[％%]?$")


def _dedup_key(item: str) -> str:
    """归一化去重键：去千分位逗号与全角差异（'4,640' 与 '4640' 同键）。"""
    return item.strip().replace(",", "").replace("，", "")


def is_pure_number(item: str) -> bool:
    return bool(_NUMBER_ITEM.match(item.strip()))


def migrate_case(case: dict) -> list[str]:
    """迁移单个用例，返回变更描述列表。"""
    changes: list[str] = []
    numbers: list[str] = []
    src = case.get("must_include") or []
    kept = []
    for item in src:
        if is_pure_number(item):
            numbers.append(item.strip())
            changes.append(f"must_include:{item}→expected_numbers")
        else:
            kept.append(item)
    if kept != src:
        case["must_include"] = kept
    if numbers:
        existing = case.get("expected_numbers") or []
        seen = {_dedup_key(n) for n in existing}
        for n in numbers:
            if _dedup_key(n) not in seen:
                existing.append(n)
                seen.add(_dedup_key(n))
        case["expected_numbers"] = existing
    return changes


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    total_changes = 0
    for path in DATASETS:
        data = json.loads(path.read_text(encoding="utf-8"))
        all_changes: list[tuple[str, list[str]]] = []
        for case in data["cases"]:
            changes = migrate_case(case)
            if changes:
                all_changes.append((case["id"], changes))
        total_changes += len(all_changes)
        print(f"== {path.name}: {len(all_changes)} 条用例迁移")
        for case_id, changes in all_changes:
            print(f"  {case_id}: {', '.join(changes)}")
        if not dry_run:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
            )
    if dry_run:
        print("(dry-run，未写盘)")
    else:
        print(f"共迁移 {total_changes} 条用例，已写盘")


if __name__ == "__main__":
    main()
