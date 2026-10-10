"""T181 审计回放 CLI（D086）：案件事件流 × 回执 × 决定书版本的不变量断言。

用法：
    uv run python -m scripts.replay_audit --case CF-2026-00000001     # 单案
    uv run python -m scripts.replay_audit --all                       # 全量批跑
    uv run python -m scripts.replay_audit --all --limit 100 --format json

退出码：0 全过；1 存在违规；2 用法错误。只读，可随时重跑。
五不变量定义见 services/audit_replay.py 模块注释与 D086。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.audit_replay import (  # noqa: E402
    INV_DESCRIPTIONS,
    audit_all,
    audit_case,
)
from services.db.session import get_session_factory  # noqa: E402


def _print_case_table(result) -> None:
    """单案/批跑共用的文本输出（result: CaseAudit）。"""
    flag = "PASS" if result.passed else "FAIL"
    print(
        f"{result.case_id}  {flag}  events={result.event_count}"
        f" jobs={result.job_count} docs={result.doc_count}"
    )
    for violation in result.violations:
        print(f"  [{violation.inv}] {violation.locator} · {violation.detail}")


def _batch_json(batch) -> str:
    payload = {
        "generated_at": batch.generated_at.isoformat(),
        "summary": {
            "cases": len(batch.cases),
            "passed": len(batch.cases) - batch.failed_count,
            "failed": batch.failed_count,
            "violations": batch.violation_count,
            "by_inv": batch.by_inv(),
        },
        "cases": [
            {
                "case_id": c.case_id,
                "passed": c.passed,
                "event_count": c.event_count,
                "job_count": c.job_count,
                "doc_count": c.doc_count,
                "violations": [
                    {"inv": x.inv, "locator": x.locator, "detail": x.detail}
                    for x in c.violations
                ],
            }
            for c in batch.cases
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def _run(args: argparse.Namespace) -> int:
    factory = get_session_factory()
    async with factory() as session:
        if args.case:
            result = await audit_case(session, args.case)
            if args.format == "json":
                print(
                    json.dumps(
                        {
                            "case_id": result.case_id,
                            "passed": result.passed,
                            "event_count": result.event_count,
                            "job_count": result.job_count,
                            "doc_count": result.doc_count,
                            "violations": [
                                {"inv": x.inv, "locator": x.locator, "detail": x.detail}
                                for x in result.violations
                            ],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                _print_case_table(result)
            return 0 if result.passed else 1

        batch = await audit_all(session, limit=args.limit)
        if args.format == "json":
            print(_batch_json(batch))
        else:
            for case_result in batch.cases:
                _print_case_table(case_result)
            print(
                f"\nreplay: cases={len(batch.cases)}"
                f" passed={len(batch.cases) - batch.failed_count}"
                f" failed={batch.failed_count} violations={batch.violation_count}"
            )
            stats = batch.by_inv()
            nonzero = {k: n for k, n in stats.items() if n}
            if nonzero:
                print("违规分布：", "，".join(f"{k}={n}" for k, n in nonzero.items()))
                for inv, count in nonzero.items():
                    print(f"  {inv}: {INV_DESCRIPTIONS[inv]}（{count}）")
        return 0 if batch.failed_count == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="案件审计事件流回放断言（只读）")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--case", help="单案回放（case_id）")
    target.add_argument("--all", action="store_true", help="全量案件批跑")
    parser.add_argument("--limit", type=int, default=None, help="批跑上限（默认全部）")
    parser.add_argument(
        "--format", choices=["table", "json"], default="table", help="输出格式"
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
