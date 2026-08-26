"""A/B 实验运行器（T040，D017）。

同一评测集分流运行多个变体（evals/variants.py 注册表：模型 / 参数 / prompt 路径 /
图谱开关），复用 test_suite 的用例执行与 metrics 聚合，产出组间对比报告：

- 任务完成率 / 工具调用准确率：差异 + 双比例 z 检验显著性粗判（|z|>1.96 ≈ p<0.05）
- 平均耗时 / LLM token 消耗：Prometheus 计数器运行前后差分（成本核算口径）
- 结果 JSON + 人读 MD 双落盘 evals/reports/

用法：
    uv run python -m evals.ab_test --variants baseline,pure_rag --limit 20
    uv run python -m evals.ab_test --variants baseline,deepseek-v4-pro --name t041_pro

第一个变体作为对照组（baseline），其余变体分别与之对比。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from app.core.logging import configure_logging, get_logger
from evals.metrics import EvalReport, aggregate, two_proportion_z_test
from evals.test_suite import build_eval_graph, load_cases, run_case
from evals.variants import VARIANTS, apply_variant

log = get_logger(__name__)

REPORTS_DIR = Path("evals/reports")


def snapshot_llm_tokens() -> dict[str, int]:
    """当前 LLM token 累计值快照（model → prompt+completion 总和，差分用）。

    只累计 `_total` 样本——Counter.collect() 还会带 `_created` 样本，
    其值是计数器创建时间戳（Unix epoch），混入会产生天文数字（实测坑）。
    """
    from services.observability import metrics

    totals: dict[str, int] = {}
    for metric in metrics.LLM_TOKENS.collect():
        for sample in metric.samples:
            if sample.name.endswith("_created"):
                continue
            model = sample.labels.get("model", "unknown")
            totals[model] = totals.get(model, 0) + int(sample.value)
    return totals


def _token_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    """两组快照的差分（after - before；只保留正值模型）。"""
    return {
        model: after.get(model, 0) - before.get(model, 0)
        for model in after
        if after.get(model, 0) - before.get(model, 0) > 0
    }


async def run_variant(variant: str, cases: list[Any]) -> dict[str, Any]:
    """应用变体 → 重建图 → 跑全量用例 → 聚合（含 token 差分与逐用例结果）。"""
    spec = apply_variant(variant)
    graph = await build_eval_graph()

    tokens_before = snapshot_llm_tokens()
    results = []
    for i, case in enumerate(cases, 1):
        # thread 按变体隔离：同进程多变体共享 checkpointer，防止上下文跨变体污染
        cr = await run_case(graph, case, thread_prefix=f"eval-{variant}")
        results.append(cr)
        mark = "PASS" if cr.passed else "FAIL"
        print(f"  [{i:>3}/{len(cases)}] {mark} {case.id} {case.user_input[:30]}")
    tokens_delta = _token_delta(tokens_before, snapshot_llm_tokens())

    report = aggregate(results)
    return {
        "spec": spec,
        "report": report,
        "tokens": tokens_delta,
        "case_results": [
            {"case_id": cr.case_id, "passed": cr.passed, "duration_s": cr.duration_s}
            for cr in results
        ],
    }


def compare_reports(base: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    """组间对比：率差异 + z 检验粗判 + 耗时/token 差（对照组 base 为基准）。"""
    b: EvalReport = base["report"]
    o: EvalReport = other["report"]
    completion = two_proportion_z_test(b.passed, b.total, o.passed, o.total)
    tool = two_proportion_z_test(
        b.tool_scored_passed, b.tool_scored_total, o.tool_scored_passed, o.tool_scored_total
    )
    return {
        "baseline": base["spec"].name,
        "variant": other["spec"].name,
        "task_completion": {
            "baseline": b.task_completion_rate,
            "variant": o.task_completion_rate,
            "delta_pp": round((o.task_completion_rate - b.task_completion_rate) * 100, 1),
            **completion,
        },
        "tool_accuracy": {
            "baseline": b.tool_accuracy,
            "variant": o.tool_accuracy,
            "delta_pp": round((o.tool_accuracy - b.tool_accuracy) * 100, 1),
            **tool,
        },
        "avg_duration_s": {
            "baseline": b.avg_duration_s,
            "variant": o.avg_duration_s,
            "delta_s": round(o.avg_duration_s - b.avg_duration_s, 2),
        },
        "llm_tokens": {
            "baseline": base["tokens"],
            "variant": other["tokens"],
            "total_baseline": sum(base["tokens"].values()),
            "total_variant": sum(other["tokens"].values()),
        },
    }


def render_md(experiment: dict[str, Any], comparisons: list[dict[str, Any]]) -> str:
    """对比报告的人读 Markdown。"""
    lines = [f"# A/B 实验报告：{experiment['name']}", ""]
    lines.append(
        f"- 数据集 `{experiment['dataset']}` v{experiment['dataset_version']}"
        f"（category={experiment['category'] or '全部'}，样本 {experiment['sample_size']} 条/变体）"
    )
    lines.append(f"- 变体：{' vs '.join(experiment['variants'])}")
    lines.append(f"- 生成时间：{experiment['generated_at']}")
    lines.append("")

    variants = experiment["variant_summaries"]
    lines.append("## 各变体指标")
    lines.append("")
    lines.append("| 变体 | 任务完成率 | 工具准确率 | 合规通过率 | 平均耗时(s) | token 消耗 |")
    lines.append("|------|-----------|-----------|-----------|------------|-----------|")
    for name, s in variants.items():
        lines.append(
            f"| {name} | {s['task_completion_rate']:.1%} | {s['tool_accuracy']:.1%} "
            f"| {s['compliance_pass_rate']:.1%} | {s['avg_duration_s']} | {s['total_tokens']} |"
        )
    lines.append("")

    lines.append("## 组间对比（对照组 = 第一个变体）")
    for c in comparisons:
        lines.append("")
        lines.append(f"### {c['baseline']} vs {c['variant']}")
        lines.append("")
        lines.append("| 指标 | 对照 | 变体 | 差异 | 显著性(p<0.05) |")
        lines.append("|------|------|------|------|----------------|")
        sig_c = "✓ 显著" if c["task_completion"]["significant_p05"] else "✗ 不显著"
        sig_t = "✓ 显著" if c["tool_accuracy"]["significant_p05"] else "✗ 不显著"
        lines.append(
            f"| 任务完成率 | {c['task_completion']['baseline']:.1%} "
            f"| {c['task_completion']['variant']:.1%} | {c['task_completion']['delta_pp']:+.1f}pp "
            f"| {sig_c}（z={c['task_completion']['z']}） |"
        )
        lines.append(
            f"| 工具准确率 | {c['tool_accuracy']['baseline']:.1%} "
            f"| {c['tool_accuracy']['variant']:.1%} | {c['tool_accuracy']['delta_pp']:+.1f}pp "
            f"| {sig_t}（z={c['tool_accuracy']['z']}） |"
        )
        d = c["avg_duration_s"]
        lines.append(f"| 平均耗时(s) | {d['baseline']} | {d['variant']} | {d['delta_s']:+} | - |")
        t = c["llm_tokens"]
        lines.append(f"| token 消耗 | {t['total_baseline']} | {t['total_variant']} | - | - |")
    lines.append("")
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description="claimflow A/B 实验运行器")
    parser.add_argument(
        "--variants",
        default="baseline,pure_rag",
        help=f"逗号分隔的变体列表（可用：{', '.join(sorted(VARIANTS))}）；第一个为对照组",
    )
    parser.add_argument("--dataset", choices=["main", "graph_assoc"], default="main")
    parser.add_argument("--category", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--name", default=None, help="实验名（报告文件名前缀，默认 ab_<变体串>）")
    args = parser.parse_args()

    configure_logging()
    variant_names = [v.strip() for v in args.variants.split(",") if v.strip()]
    if len(variant_names) < 2:
        raise SystemExit("至少需要两个变体（对照组 + 实验组）")

    cases, version = load_cases(args.dataset, args.category, args.limit)
    print(f"A/B 实验：{variant_names} × {len(cases)} 条用例（dataset={args.dataset} v{version}）")

    runs: dict[str, dict[str, Any]] = {}
    for v in variant_names:
        print(f"\n===== 变体 {v} =====")
        runs[v] = await run_variant(v, cases)

    # 组间对比：其余变体分别与对照组比
    comparisons = [compare_reports(runs[variant_names[0]], runs[v]) for v in variant_names[1:]]

    experiment = {
        "name": args.name or f"ab_{'_'.join(variant_names)}",
        "dataset": args.dataset,
        "dataset_version": version,
        "category": args.category,
        "sample_size": len(cases),
        "variants": variant_names,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "variant_summaries": {
            v: {
                **runs[v]["report"].model_dump(exclude={"failures"}),
                "total_tokens": sum(runs[v]["tokens"].values()),
                "tokens_by_model": runs[v]["tokens"],
                "description": runs[v]["spec"].description,
            }
            for v in variant_names
        },
    }
    payload = {
        "experiment": experiment,
        "comparisons": comparisons,
        "case_results": {v: runs[v]["case_results"] for v in variant_names},
    }

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    json_path = REPORTS_DIR / f"{experiment['name']}_{stamp}.json"
    md_path = REPORTS_DIR / f"{experiment['name']}_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_md(experiment, comparisons), encoding="utf-8")

    print("\n===== A/B 对比摘要 =====")
    for c in comparisons:
        tc = c["task_completion"]
        sig = "显著" if tc["significant_p05"] else "不显著"
        print(
            f"{c['baseline']} → {c['variant']}: 完成率 {tc['baseline']:.1%} → {tc['variant']:.1%}"
            f"（{tc['delta_pp']:+.1f}pp, {sig} z={tc['z']}）"
        )
        t = c["llm_tokens"]
        print(f"  token: {t['total_baseline']} → {t['total_variant']}；耗时: ", end="")
        d = c["avg_duration_s"]
        print(f"{d['baseline']}s → {d['variant']}s")
    print(f"报告已写入: {json_path} / {md_path}")


if __name__ == "__main__":
    asyncio.run(main())
