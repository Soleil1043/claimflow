"""交付队列负载测试（T150，P1-1）：量化单消费者边界，供 D044 SKIP LOCKED 升级位提供触发证据。

用法（服务需已启动；零 LLM 口径需**双开关**，消除编排与材料 AI 审查的网络往返）：

    ORCHESTRATOR_LLM_ENABLED=false MATERIAL_REVIEW_LLM_ENABLED=false \
        LLM_API_KEY=sk-x uv run uvicorn app.main:app --port 8000
    uv run python scripts/load_test.py                     # 50 案 × 并发 10
    uv run python scripts/load_test.py --cases 100 --concurrency 20

口径：
- 案件体：零材料 + 时间戳 user_id（幂等键唯一）→ 零材料必然挂起
  supplement_pending（走完 intake + 材料审核规则层 + 交付队列真实消费）
- 停止条件：status 进入 {supplement_pending, auto_issued, referred, closed}
  （不再自行流转的稳定态）
- 积压 = 客户端视角（已提交 − 已稳定）随时间序列，峰值即队列积压深度

产出：evals/reports/t150_load_test.json + stdout 摘要。退出码 0=全部到达稳定态。
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "evals" / "reports" / "t150_load_test.json"
STABLE_STATUSES = {"supplement_pending", "auto_issued", "referred", "closed"}
POLL_INTERVAL_S = 0.25
POLL_TIMEOUT_S = 600.0
# 轮询并发上限：模拟真实门户行为（前端不会 N 案全量同时轮询）；也避免把
# 服务端连接池直接打爆——T150 实测 50 路全量轮询撞默认池 15 上限（已扩池，
# 这里仍限流保持压测口径贴近真实负载形态）
POLL_CONCURRENCY = 10


def _pct(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), round(pct * len(ordered))))
    return round(ordered[rank - 1], 2)


async def submit_one(
    client: httpx.AsyncClient, tag: str, idx: int
) -> tuple[str, float]:
    """提交单案（受理即返回路径），返回 (case_id, 提交耗时 s)。"""
    body = {
        "user_id": f"load-{tag}-{idx}",
        "policy_no": "POL-2025-0001",
        "claimed_amount": "15800.00",
        "incident_date": "2026-08-10",
        "incident_description": "负载测试案件（急性阑尾炎住院）。",
        "materials": [],
    }
    started = time.perf_counter()
    resp = await client.post("/api/v1/cases", json=body)
    elapsed = time.perf_counter() - started
    if resp.status_code != 201:
        msg = f"提交失败 {resp.status_code}: {resp.text[:200]}"
        raise RuntimeError(msg)
    return resp.json()["case_id"], elapsed


async def poll_until_stable(
    client: httpx.AsyncClient, case_id: str
) -> tuple[str, int]:
    """轮询至稳定态，返回 (status, 轮询次数)。"""
    polls = 0
    deadline = time.perf_counter() + POLL_TIMEOUT_S
    while time.perf_counter() < deadline:
        resp = await client.get(f"/api/v1/cases/{case_id}")
        polls += 1
        status = resp.json().get("status", "")
        if status in STABLE_STATUSES:
            return status, polls
        await asyncio.sleep(POLL_INTERVAL_S)
    msg = f"{case_id} 轮询超时（{POLL_TIMEOUT_S}s）"
    raise TimeoutError(msg)


async def run(base_url: str, cases: int, concurrency: int, out: str | None) -> int:
    tag = str(int(time.time()))
    sem = asyncio.Semaphore(concurrency)
    submit_times: dict[str, float] = {}
    settle_times: dict[str, float] = {}
    statuses: dict[str, str] = {}
    poll_counts: dict[str, int] = {}
    backlog_series: list[tuple[float, int]] = []  # (相对开始 s, 积压深度)
    errors: list[str] = []

    async with httpx.AsyncClient(base_url=base_url, timeout=60) as client:
        health = (await client.get("/health")).json()
        print(f"[0] health={health['status']} profile={health.get('profile')}")
        print(f"[1] 提交 {cases} 案件（并发 {concurrency}）…")

        t0 = time.perf_counter()

        async def _submit(idx: int) -> None:
            async with sem:
                try:
                    case_id, elapsed = await submit_one(client, tag, idx)
                    submit_times[case_id] = elapsed
                    settle_times[case_id] = time.perf_counter() - t0  # 先记提交完成时刻
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"submit-{idx}: {exc}")

        await asyncio.gather(*(_submit(i) for i in range(cases)))
        submit_wall = time.perf_counter() - t0
        submitted = len(submit_times)
        print(f"[2] 提交完成：{submitted}/{cases} 成功，墙钟 {submit_wall:.2f}s"
              f"（吞吐 {submitted / submit_wall:.1f} 案/s）")

        # 轮询：限流并发等全部稳定，同时采样积压
        poll_sem = asyncio.Semaphore(POLL_CONCURRENCY)

        async def _poll(case_id: str) -> None:
            async with poll_sem:
                try:
                    status, polls = await poll_until_stable(client, case_id)
                    statuses[case_id] = status
                    poll_counts[case_id] = polls
                    settle_times[case_id] = time.perf_counter() - t0
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"poll-{case_id}: {exc}")

        async def _sample_backlog() -> None:
            while len(statuses) < submitted:
                backlog_series.append(
                    (round(time.perf_counter() - t0, 2), submitted - len(statuses))
                )
                await asyncio.sleep(0.5)

        sampler = asyncio.create_task(_sample_backlog())
        await asyncio.gather(*(_poll(cid) for cid in submit_times))
        sampler.cancel()
        total_wall = time.perf_counter() - t0

    settled = len(statuses)
    e2e = sorted(settle_times.values())
    peak_backlog = max((d for _, d in backlog_series), default=0)
    settle_span = e2e[-1] - submit_wall if e2e else 0.0

    report = {
        "task": "T150 交付队列负载测试",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "params": {"cases": cases, "concurrency": concurrency, "base_url": base_url},
        "results": {
            "submitted": submitted,
            "settled": settled,
            "errors": errors[:20],
            "submit_throughput_per_s": round(submitted / submit_wall, 2) if submit_wall else 0,
            "submit_wall_s": round(submit_wall, 2),
            "delivery_throughput_per_min": (
                round(settled / settle_span * 60, 2) if settle_span > 0 else None
            ),
            "settle_span_s": round(settle_span, 2),
            "total_wall_s": round(total_wall, 2),
            "peak_backlog": peak_backlog,
            "e2e_latency_p50_s": _pct(e2e, 0.50),
            "e2e_latency_p95_s": _pct(e2e, 0.95),
            "e2e_latency_max_s": round(e2e[-1], 2) if e2e else 0,
            "polls_total": sum(poll_counts.values()),
            "status_distribution": {
                s: sum(1 for v in statuses.values() if v == s)
                for s in sorted(set(statuses.values()))
            },
        },
        "backlog_series_sampled": backlog_series[:: max(1, len(backlog_series) // 50)],
    }

    out_path = Path(out) if out else REPORT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    r = report["results"]
    print(f"\n{'=' * 60}")
    print(f"[3] 稳定态：{settled}/{cases}（分布 {r['status_distribution']}）")
    print(f"    提交吞吐：{r['submit_throughput_per_s']} 案/s（墙钟 {r['submit_wall_s']}s）")
    print(f"    交付吞吐：{r['delivery_throughput_per_min']} 案/min"
          f"（排空跨度 {r['settle_span_s']}s）")
    print(f"    峰值积压：{peak_backlog} 案")
    print(f"    端到端延迟：P50 {r['e2e_latency_p50_s']}s / P95 {r['e2e_latency_p95_s']}s"
          f" / max {r['e2e_latency_max_s']}s")
    print(f"    轮询总次数：{r['polls_total']}")
    if errors:
        print(f"    ⚠️ 错误 {len(errors)} 条：{errors[:5]}")
    print(f"报告 → {out_path}")
    return 0 if settled == cases and not errors else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="交付队列负载测试（T150）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--cases", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.base_url, args.cases, args.concurrency, args.out)))


if __name__ == "__main__":
    main()
