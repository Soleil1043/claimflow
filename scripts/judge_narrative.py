"""T178 叙述质量 judge × 人工抽评对账 CLI（D084：观测层，不进门禁）。

用法（需真实 LLM Key；不进 CI——judge 是观测工具不是门禁）：
    uv run python -m scripts.judge_narrative                      # 全量采样样本
    uv run python -m scripts.judge_narrative --limit 20           # 最近 20 条
    uv run python -m scripts.judge_narrative --out evals/reports/xxx.json

产出：对账报告（一致率 / 分歧清单 / judge-only 分布与低于阈值清单）。
解读：一致率 = judge 质量的度量（人工是金标准）；低于阈值清单 = 坐席扩大
抽评的候选；报告不产生任何阻断。
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.narrative_judge import (  # noqa: E402
    JUDGE_DIMENSIONS,
    JUDGE_PASS_TOTAL,
    collect_samples,
    judge_narrative,
    reconcile,
)


async def run(limit: int, out: str | None) -> int:
    from app.core.config import settings
    from services.db.session import get_session_factory

    async with get_session_factory()() as session:
        samples = await collect_samples(session)

    if limit > 0:
        samples = samples[-limit:]  # 最近采样的 N 条
    if not samples:
        print("无叙述抽评样本（narrative_sample 事件为空）——先跑自动签发案件")
        return 0

    print(f"采集 {len(samples)} 条样本，judge 判分中（{settings.llm_model}，温度 0）…")
    judged: list[dict] = []
    for sample in samples:
        try:
            result = await judge_narrative(sample)
        except Exception as exc:  # noqa: BLE001 单样本失败不中断（记 errors）
            judged.append(
                {"case_id": sample["case_id"], "human": sample.get("human"),
                 "judge": None, "error": str(exc)[:200]}
            )
            continue
        judged.append(
            {"case_id": sample["case_id"], "human": sample.get("human"), "judge": result}
        )
        mark = "✓" if result["pass"] else "✗"
        print(f"  [{mark}] {sample['case_id']} total={result['total']} {result['rationale'][:60]}")

    report = {
        "task": "T178 叙述质量 LLM judge × 人工抽评对账（D084：观测层，不进门禁）",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "model": settings.llm_model,
        "rubric": {
            "dimensions": list(JUDGE_DIMENSIONS),
            "scale": "0-2",
            "pass": f"total>={JUDGE_PASS_TOTAL}",
        },
        "variance_note": (
            "温度 0 单次判分仍有方差（RAGAS 自实现同款口径）；一致率按案比较"
            "（pass/pass）不逐分比较，容忍 ±1 分抖动"
        ),
        **reconcile(judged),
    }

    report_path = Path(out) if out else ROOT / "evals" / "reports" / "t178_judge_reconciliation.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    rec = report["reconciliation"]
    only = report["judge_only"]
    print(f"\n{'=' * 50}")
    if rec["judged_with_human"]:
        print(f"对账：{rec['agreed']}/{rec['judged_with_human']} 一致"
              f"（一致率 {rec['agreement_rate']}，分歧 {len(rec['disagreements'])} 条）")
    else:
        print("对账：暂无人工 verdict（judge-only 观测模式）")
    print(f"judge-only：{only['count']} 条，低于阈值 {len(only['below_threshold'])} 条"
          "（坐席扩大抽评候选）")
    print(f"报告 → {report_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="叙述质量 judge × 人工抽评对账（观测层）")
    parser.add_argument("--limit", type=int, default=0, help="只判最近 N 条采样（0=全量）")
    parser.add_argument("--out", default=None, help="报告输出路径（默认 t178_judge_reconciliation.json）")
    args = parser.parse_args()
    return asyncio.run(run(args.limit, args.out))


if __name__ == "__main__":
    sys.exit(main())
