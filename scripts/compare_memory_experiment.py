"""T100 记忆注入实验聚合：A 基线 vs B（memory_routing+seed）逐案对比。

用法：uv run python scripts/compare_memory_experiment.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name: str) -> dict:
    return json.loads((ROOT / "evals/reports" / name).read_text(encoding="utf-8"))


def main() -> None:
    a = load("exp_A_full.json")
    b = load("exp_B_full.json")

    print("=" * 60)
    print("T100 申请人记忆注入实验（全 132 案，LLM 模式）")
    print("=" * 60)
    for label, r in (("A 基线（无记忆）", a), ("B 实验（记忆注入）", b)):
        g = r["gates"]
        print(
            f"{label}: route={g['route_consistency']['value']:.4f} "
            f"liability={g['liability_consistency']['value']:.4f} "
            f"amount={g['amount_accuracy']['value']:.4f} "
            f"budget={g['max_routing_calls']['value']} "
            f"总一致率={r['consistency']:.4f}"
        )

    def fail_map(r: dict) -> dict[str, list[str]]:
        return {
            f["case_id"]: sorted(d for d, v in (f.get("checks") or {}).items() if v is False)
            for f in r["failures"]
        }

    fa, fb = fail_map(a), fail_map(b)
    recovered = sorted(set(fa) - set(fb))
    regressed = sorted(set(fb) - set(fa))
    both = sorted(set(fa) & set(fb))
    print(f"\nA 失败 {len(fa)} 案 / B 失败 {len(fb)} 案 / 共同失败 {len(both)} 案")
    print("恢复（A 失败 B 通过）:", recovered or "无")
    print("退化（A 通过 B 失败）:", regressed or "无")
    print("共同失败:", both or "无")
    if regressed:
        print("\n退化明细：")
        for cid in regressed:
            print(f"  {cid}: dims={fb[cid]}")


if __name__ == "__main__":
    main()
