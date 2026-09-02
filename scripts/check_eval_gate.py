"""评测回归门禁（T072，GAP-007）：CI smoke 报告 vs 基线，完成率降幅 >5pp 拦截。

用法（CI / 本地）：
    uv run python scripts/check_eval_gate.py evals/reports/ci_report.json \
        [--baseline evals/reports/baseline.json]

退出码：0=放行（降幅 ≤5pp 或无基线不拦截）；1=拦截（含报告不可读）。
统计提示（GAP-008）：输出当前完成率的 Wilson 95% CI——n=20 的 smoke 半宽 >±15pp，
门禁只拦"明显退步"，边际差异看 nightly 全量。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 完成率降幅阈值（百分点）：超过即拦截合并
DROP_THRESHOLD_PP = 5.0


def load_rate(path: str | Path) -> tuple[float, int, int]:
    """读评测报告 JSON → (task_completion_rate, passed, total)；不可读抛 SystemExit。"""
    p = Path(path)
    try:
        data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        summary = data.get("summary") or {}
        rate = float(summary["task_completion_rate"])
        passed = int(summary.get("passed", round(rate * int(summary["total"]))))
        total = int(summary["total"])
        return rate, passed, total
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"❌ 报告不可读或缺少 summary.task_completion_rate：{path}（{exc}）") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="评测回归门禁（完成率降幅 >5pp 拦截）")
    parser.add_argument("report", help="当前评测报告 JSON 路径（如 ci_report.json）")
    parser.add_argument(
        "--baseline", default="evals/reports/baseline.json", help="基线报告路径（默认主基线）"
    )
    args = parser.parse_args()

    cur_rate, cur_passed, cur_total = load_rate(args.report)

    baseline = Path(args.baseline)
    if not baseline.exists():
        print(f"⚠️ 基线不存在（{baseline}），门禁不拦截，仅记录当前值")
        print(f"   当前完成率 {cur_rate:.1%}（{cur_passed}/{cur_total}）")
        return

    base_rate, base_passed, base_total = load_rate(baseline)

    from evals.metrics import wilson_ci

    ci = wilson_ci(cur_passed, cur_total)
    drop_pp = (base_rate - cur_rate) * 100
    print(f"基线完成率: {base_rate:.1%}（{base_passed}/{base_total}）")
    print(f"当前完成率: {cur_rate:.1%}（{cur_passed}/{cur_total}）Wilson 95% CI [{ci[0]:.1%}, {ci[1]:.1%}]")
    print(f"降幅: {drop_pp:+.1f}pp（阈值 {DROP_THRESHOLD_PP}pp）")

    if drop_pp > DROP_THRESHOLD_PP:
        print(
            "❌ 评测门禁拦截：完成率显著回退。CI 区间提示：小样本噪声大，"
            "如需精确判定请跑全量对比。"
        )
        sys.exit(1)
    print("✅ 评测门禁放行")


if __name__ == "__main__":
    main()
