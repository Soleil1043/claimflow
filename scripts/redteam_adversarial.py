"""T159 对抗集 hold-out 红队工具箱（D073）。

四个子命令（可组合）：
    --split      把 adversarial.json 的 robustness 层拆到 holdout（幂等：已拆分则从 holdout 找回种子）
    --generate   用 evals/redteam.py 变异器生成红队变体并入 holdout（幂等：先清旧 RTV-* 再重生成）
    --probe      holdout 全量过编译图（复用评测门运行器；--llm 开 LLM Orchestrator，--out 指定报告路径）
    --archive-md 生成 8 条注入样本原文归档 + 变体清单 + 探测结论 → evals/reports/t159_redteam_evidence.md

用法：
    uv run python -m scripts.redteam_adversarial --split --generate --archive-md
    uv run python -m scripts.redteam_adversarial --probe --out evals/reports/t159_redteam_probe_det.json
    uv run python -m scripts.redteam_adversarial --probe --llm --out evals/reports/t159_redteam_probe_llm.json

纪律（D073）：holdout 不进 push CI（保持盲测性），nightly-llm 周期跑；红了修
一般化机制（关键词/守卫/合规门）并用 --generate 重生成新变体复测，禁止按 case_id 打补丁。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.redteam import CLEAN_CORES, MUTATORS, generate_variants  # noqa: E402

ADV_PATH = ROOT / "evals" / "datasets" / "adjudication_adversarial.json"
HOLDOUT_PATH = ROOT / "evals" / "datasets" / "adjudication_adversarial_holdout.json"
EVIDENCE_PATH = ROOT / "evals" / "reports" / "t159_redteam_evidence.md"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"written: {path.relative_to(ROOT)}")


def cmd_split() -> int:
    """robustness 层拆入 holdout；已拆分过则从 holdout 找回种子（幂等）。"""
    adv = _load(ADV_PATH)
    injection = [c for c in adv["cases"] if c["expected"]["category"] == "injection"]
    robust = [c for c in adv["cases"] if c["expected"]["category"] == "robustness"]
    if not robust:
        if not HOLDOUT_PATH.exists():
            print("错误：adversarial.json 已无 robustness 且 holdout 不存在", file=sys.stderr)
            return 1
        hold = _load(HOLDOUT_PATH)
        robust = [c for c in hold["cases"] if c["expected"]["category"] == "robustness"]

    adv["cases"] = injection
    adv["_meta"] = {
        "task": "T124 核赔对抗回归集（T159 拆分后：injection 硬门层）",
        "tiers": {
            "injection": "注入/诱导类：期望=正确结果，进硬门（结构化字段与守卫不被文本操纵）"
        },
        "说明": (
            "独立数据集不污染主基线（D033 口径延续）；--dataset adversarial 运行。"
            "robustness 同义词层已移入 adversarial_holdout.json（T159 盲测集——关键词路径"
            "最易对 fixture 过拟合）；红队变体生成见 evals/redteam.py（D073）"
        ),
    }
    holdout = {
        "_meta": {
            "task": "T159 对抗集 hold-out 盲测集（红队自动生成）",
            "discipline": (
                "不进 push CI（保持盲测性），nightly-llm 周期跑；红了修一般化机制"
                "（关键词/守卫/合规门）并重生成新变体复测，禁止按 case_id 打补丁"
            ),
            "tiers": {
                "robustness": "同义词变体（自 adversarial.json 移入：确定性关键词已知缺口）",
                "injection": "注入/诱导类变体：干净事实核 + 攻击包裹，期望逐字段继承种子案",
            },
            "provenance": {},
        },
        "cases": robust,
        "frequency_signals": adv.get("frequency_signals", []),
    }
    _write(ADV_PATH, adv)
    _write(HOLDOUT_PATH, holdout)
    print(f"拆分完成：injection {len(injection)} 案留守 / robustness {len(robust)} 案入 holdout")
    return 0


def cmd_generate(per_seed: int) -> int:
    """生成红队变体并入 holdout（幂等：先清旧 RTV-* 再重生成）。"""
    adv = _load(ADV_PATH)
    hold = _load(HOLDOUT_PATH)
    seeds = [c for c in adv["cases"] if c["expected"]["category"] == "injection"]
    if not seeds:
        print("错误：adversarial.json 无 injection 种子（先跑 --split）", file=sys.stderr)
        return 1
    variants, provenance = generate_variants(seeds, per_seed=per_seed)
    keep = [c for c in hold["cases"] if not c["case_id"].startswith("RTV-")]
    hold["cases"] = keep + variants
    hold["_meta"]["provenance"] = provenance
    _write(HOLDOUT_PATH, hold)
    print(
        f"生成 {len(variants)} 条变体（{per_seed}/种子 × {len(seeds)} 种子），holdout 共 {len(hold['cases'])} 案"
    )
    return 0


def cmd_probe(use_llm: bool, out_path: str | None) -> int:
    """holdout 全量过编译图——复用评测门运行器（_run_suite），门禁口径一致。"""
    from evals.adjudication_suite import _run_suite

    default = ROOT / "evals" / "reports" / f"t159_redteam_probe_{'llm' if use_llm else 'det'}.json"
    print(f"探测 holdout（mode={'llm' if use_llm else 'deterministic'}）→ {default}")
    return asyncio.run(
        _run_suite(
            None, use_llm, out_path=out_path or str(default), dataset_name="adversarial_holdout"
        )
    )


def cmd_archive_md() -> int:
    """生成证据归档：8 条注入样本原文逐字引用 + 分类法映射 + holdout 组成 + 探测结论。"""
    adv = _load(ADV_PATH)
    hold = _load(HOLDOUT_PATH) if HOLDOUT_PATH.exists() else None
    lines: list[str] = []
    lines.append("# T159 对抗集红队证据归档（8 条注入样本原文 + hold-out 组成）\n")
    lines.append(
        "> 生成：`uv run python -m scripts.redteam_adversarial --archive-md`；"
        "决策依据 D073；纪律：holdout 不进 push CI，nightly-llm 周期盲测。\n"
    )

    lines.append("## 一、8 条注入样本原文（逐字归档，与 adjudication_adversarial.json 一致）\n")
    lines.append(
        "期望语义：注入案的期望是**正确结果**（结构化字段与公式不被描述文本操纵），"
        '不是"期望被拒"的悲观假设——注入得逞才是异常。\n'
    )
    for c in adv["cases"]:
        e = c["expected"]
        lines.append(
            f"### {c['case_id']}（claimed={c['claimed_amount']} → 期望 "
            f"{e['route']}/{e['liability']}/{e['approved_amount']}）\n"
        )
        lines.append(f"- 分类：{e['category']}；要点：{e['note']}")
        lines.append(f"- **原文**：`{c['incident_description']}`\n")

    lines.append("## 二、攻击分类法与框架溯源（evals/redteam.py MUTATORS）\n")
    lines.append(
        "自研变异器，分类法对齐主流红队框架（未引入框架本体的理由见 decisions.md D073）；\n"
        "载荷为纯文本，可直接喂给 garak/pyrit 做模型级探测。\n"
    )
    lines.append("| mutator | family | framework_ref | 说明 |")
    lines.append("|---|---|---|---|")
    for m in MUTATORS:
        lines.append(f"| {m.mutator_id} | {m.family} | `{m.framework_ref}` | {m.description} |")
    lines.append("")

    if hold:
        prov = hold["_meta"].get("provenance", {})
        robust = [c for c in hold["cases"] if c["expected"]["category"] == "robustness"]
        variants = [c for c in hold["cases"] if c["case_id"].startswith("RTV-")]
        lines.append(f"## 三、hold-out 组成（共 {len(hold['cases'])} 案）\n")
        lines.append(
            f"- robustness 种子移入：{len(robust)} 案（{'、'.join(c['case_id'] for c in robust)}）"
        )
        lines.append(f"- 红队变体：{len(variants)} 条（{len(CLEAN_CORES)} 种子 × 轮转变异器）\n")
        lines.append("| 变体 | 种子 | mutator | framework_ref | family |")
        lines.append("|---|---|---|---|---|")
        for v in variants:
            p = prov.get(v["case_id"], {})
            lines.append(
                f"| {v['case_id']} | {p.get('source_case', '?')} | "
                f"{p.get('mutator', '?')} | `{p.get('framework_ref', '?')}` | "
                f"{p.get('family', '?')} |"
            )
        lines.append("")

    # 探测结论（若跑过 --probe）
    lines.append("## 四、探测结论（编译图即靶标，判定复用评测门 score_case 五维）\n")
    for mode, label in (("det", "确定性（零 LLM）"), ("llm", "LLM Orchestrator")):
        rp = ROOT / "evals" / "reports" / f"t159_redteam_probe_{mode}.json"
        if not rp.exists():
            lines.append(f"- {label}：未运行（`--probe{' --llm' if mode == 'llm' else ''}`）")
            continue
        r = json.loads(rp.read_text(encoding="utf-8"))
        lines.append(
            f"- **{label}**：{r['matched']}/{r['total_cases']} 一致"
            f"（consistency={r['consistency']}，overall_passed={r['overall_passed']}）；"
            f"tokens/案均值 {r['cost']['tokens_per_case_avg']}"
        )
        for f in r.get("failures", []):
            lines.append(f"  - 失败：{f['case_id']}（{f['category']}）checks={f['checks']}")

    EVIDENCE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"written: {EVIDENCE_PATH.relative_to(ROOT)}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="T159 对抗集 hold-out 红队工具箱")
    parser.add_argument("--split", action="store_true", help="robustness 层拆入 holdout（幂等）")
    parser.add_argument("--generate", action="store_true", help="生成红队变体并入 holdout（幂等）")
    parser.add_argument("--per-seed", type=int, default=3, help="每个种子的变体数（默认 3）")
    parser.add_argument("--probe", action="store_true", help="holdout 全量过编译图（评测门口径）")
    parser.add_argument("--llm", action="store_true", help="probe 启用 LLM Orchestrator")
    parser.add_argument("--out", default=None, help="probe 报告输出路径")
    parser.add_argument("--archive-md", action="store_true", help="生成证据归档 markdown")
    args = parser.parse_args()

    rc = 0
    if args.split:
        rc = cmd_split() or rc
    if args.generate:
        rc = cmd_generate(args.per_seed) or rc
    if args.probe:
        rc = cmd_probe(args.llm, args.out) or rc
    if args.archive_md:
        rc = cmd_archive_md() or rc
    if not any([args.split, args.generate, args.probe, args.archive_md]):
        parser.print_help()
        rc = 1
    sys.exit(rc)


if __name__ == "__main__":
    main()
