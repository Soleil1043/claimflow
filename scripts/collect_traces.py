"""T069 轨迹参考收集脚本：跑 multi_step 全量，落盘每条用例的实际工具序列与 Agent 路由。

审计 P1-3 建议的半自动标注辅助——"从实际运行的 tool_trace 提取高频序列供人工确认"。
产出 evals/reports/t069_trace_reference.json：[{id, input, tools, route, passed, error}]，
供 scripts/annotate_trajectory.py 生成 order/route 标注草案。

用法：uv run python scripts/collect_traces.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from evals.test_suite import build_eval_graph, load_cases, run_case

OUT = Path("evals/reports/t069_trace_reference.json")


async def main() -> None:
    cases, version = load_cases("main", "multi_step", None)
    print(f"收集 multi_step {len(cases)} 条轨迹（v{version}）…", flush=True)
    graph = await build_eval_graph()
    rows = []
    for i, case in enumerate(cases, 1):
        cr = await run_case(graph, case)
        rows.append(
            {
                "id": case.id,
                "input": case.user_input,
                "tools": [str(t.get("tool")) for t in cr.tool_trace],
                "route": cr.agent_route,
                "passed": cr.passed,
                "error": cr.error,
            }
        )
        print(
            f"[{i:>2}/{len(cases)}] {case.id} route={cr.agent_route} "
            f"tools={rows[-1]['tools']}",
            flush=True,
        )
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"轨迹参考已写入 {OUT}")

    from services.memory.short_term import get_checkpoint_manager

    await get_checkpoint_manager().close()


if __name__ == "__main__":
    asyncio.run(main())
