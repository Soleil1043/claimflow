"""T156 双实例 live 冒烟（D072）：跨实例补件恢复实测。

对**两个共享存储的实例**执行多实例专属链路（材料构造/轮询/口径复用
scripts.verify_adjudication）：
1. 跨实例主链路：A 提交（零材料挂起补件）→ B 上传三份 Word 补齐 → 案件到终态；
   断言 **timeline 零 schema_reset**——checkpoint 未共享时他实例认领 RESUME 会触发
   版本门卫降级全新重跑（schema_reset 落审计且案件回 supplement_pending 永不到终态），
   故"终态 + 零 schema_reset"即跨实例 checkpoint 命中的证明，无需感知认领方是谁
2. 双向对称：B 提交 → A 补件 → 终态 + 零 schema_reset
3. 跨实例可见性：A 受理的案件 B 立即可查（共享业务库）

两种用法：
- 已有两实例：uv run python -m scripts.verify_multiinstance --base-url-a ... --base-url-b ...
- 本地自起（--boot）：脚本拉起两个 uvicorn（同 cwd 共享业务库/uploads；
  CHECKPOINT_BACKEND=sqlite 共享 checkpoint；每实例独立 QDRANT_LOCAL_PATH——
  qdrant local 不支持双进程同目录；ORCHESTRATOR_LLM_ENABLED=false 对齐 CI 口径）
退出码：0=全通过，1=有失败。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
import urllib.request
from decimal import Decimal

import httpx

from scripts.verify_adjudication import (
    _MATERIAL_TEXTS,
    _staff_headers,
    upload_material,
    wait_job_done,
    wait_settled,
    wait_terminal,
)

passed = 0
failed = 0

TERMINAL = {"auto_issued", "referred", "closed"}

_HEALTH_TIMEOUT_S = 240  # boot 档首次含 BGE 预热 + dev 建表


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}", flush=True)
    else:
        failed += 1
        print(f"  ❌ {name} {detail}", flush=True)


def _port_in_use(port: int) -> bool:
    """预检端口占用：boot 前置拦截残留实例（残留进程会冒充新实例应答 health，
    让冒烟跑在"幽灵+新"组合上，掩蔽进程回收缺陷——实测实锤）。"""
    import socket

    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_health(base_url: str, timeout_s: float = _HEALTH_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=5) as resp:
                if resp.status == 200 and b'"status":"ok"' in resp.read():
                    return True
        except Exception:  # noqa: BLE001 —— 未就绪继续等
            pass
        time.sleep(2)
    return False


def _timeline_kinds(detail: dict) -> set[str]:
    return {e.get("kind", "") for e in detail.get("timeline", [])}


async def _cross_instance_flow(
    ac_a: httpx.AsyncClient,
    ac_b: httpx.AsyncClient,
    *,
    submit_label: str,
    user_id: str,
    offline: bool,
) -> None:
    """一组跨实例链路：submit 端提交 → 对端补件 → 终态 + 零 schema_reset。"""
    body = {
        "user_id": user_id,
        "policy_no": "POL-2025-0001",
        "claimed_amount": "15800.00",
        "incident_date": "2026-08-10",
        "incident_description": "急性阑尾炎住院手术，共花费15800元。",
        "materials": [],  # 零材料提交 → 补件挂起 → 对端逐份上传补齐
    }
    resp = await ac_a.post("/api/v1/cases", json=body)
    check(f"[{submit_label}] 提交返回 201", resp.status_code == 201, f"got {resp.status_code}")
    case_id = resp.json().get("case_id", "")

    # 提交端的任务由任一实例认领（租约互斥，无归属假设）——等待停稳只看案件状态
    detail = await wait_settled(ac_a, case_id)
    check(f"[{submit_label}] 零材料提交挂起补件", detail.get("status") == "supplement_pending",
          f"got {detail.get('status')}")

    # 跨实例可见性：对端立刻能查到同一案件与交付凭证（共享业务库）
    cross = await ac_b.get(f"/api/v1/cases/{case_id}")
    cross_detail = cross.json() if cross.status_code == 200 else {}
    check(f"[{submit_label}] 对端可见案件与凭证", cross.status_code == 200
          and cross_detail.get("job") is not None, f"got {cross.status_code}")

    # 对端补件：三份真实 Word（与单实例冒烟同口径），逐份等任务停稳再传
    sources: dict[str, str] = {}
    for doc_type in _MATERIAL_TEXTS:
        if detail.get("status") != "supplement_pending":
            break  # 已进终态，无需继续补件
        code, up = await upload_material(ac_b, case_id, doc_type)
        check(f"[{submit_label}] 对端上传 {doc_type}.docx 200", code == 200, f"got {code}")
        sources[doc_type] = str(up.get("source") or "")
        await wait_job_done(ac_b, case_id)
        detail = await wait_settled(ac_b, case_id)

    detail = await wait_terminal(ac_a, case_id)
    if offline:
        check(f"[{submit_label}] 三份材料均降级 Mock 提取（零 Key 预期）",
              all(s == "mock_fallback" for s in sources.values()), f"sources={sources}")
        check(f"[{submit_label}] 零 Key 转人工裁量（置信度不足）",
              detail.get("status") == "referred", f"got {detail.get('status')}")
    else:
        check(f"[{submit_label}] 三份材料均走真实提取（text_model）",
              all(s == "text_model" for s in sources.values()), f"sources={sources}")
        check(f"[{submit_label}] 案件状态 auto_issued", detail.get("status") == "auto_issued",
              f"got {detail.get('status')}")
        approved = str(detail.get("approved_amount") or "")
        check(f"[{submit_label}] 核定金额 4640.00（实际 {approved}）",
              approved and Decimal(approved) == Decimal("4640.00"))

    # 跨实例证明核心：全程无 schema_reset（checkpoint 共享命中，resume 未降级重跑）
    kinds = _timeline_kinds(detail)
    check(f"[{submit_label}] 零 schema_reset（跨实例 checkpoint 命中）",
          "schema_reset" not in kinds, f"kinds={sorted(k for k in kinds if k)}")


async def verify(base_url_a: str, base_url_b: str, *, offline: bool) -> None:
    tag = str(int(time.time()))
    headers = _staff_headers()
    async with (
        httpx.AsyncClient(base_url=base_url_a, timeout=60, headers=headers) as ac_a,
        httpx.AsyncClient(base_url=base_url_b, timeout=60, headers=headers) as ac_b,
    ):
        print(f"\n--- 1. 跨实例主链路：A 提交 → B 补件（{'离线兜底档' if offline else '完整档'}） ---",
              flush=True)
        await _cross_instance_flow(
            ac_a, ac_b, submit_label="A→B", user_id=f"mi-ab-{tag}", offline=offline
        )

        print("\n--- 2. 双向对称：B 提交 → A 补件 ---", flush=True)
        await _cross_instance_flow(
            ac_b, ac_a, submit_label="B→A", user_id=f"mi-ba-{tag}", offline=offline
        )

    print(f"\n{'=' * 40}")
    print(f"双实例验证：{passed} 通过 / {failed} 失败")
    print(f"{'=' * 40}")
    if failed > 0:
        sys.exit(1)


# ===== --boot 模式：本地自起两个实例（一条命令复现 T145 场景） =====


def _boot_instance(port: int, tag: str, *, offline: bool) -> subprocess.Popen:
    """拉起一个 dev 实例：同 cwd 共享业务库/uploads + sqlite 共享 checkpoint。

    用 `sys.executable -m uvicorn` 直起（不经 `uv run` 包装）——Windows 下
    terminate() 只能命中直接子进程，经 uv 包装时 uvicorn 孙进程会变孤儿占住
    端口（实测实锤）；单进程形态保证回收可靠。
    """
    env = os.environ.copy()
    env.update(
        {
            # T155 多实例契约：共享 checkpoint 文件（缺省 memory 会复现 T145 重跑）
            "CHECKPOINT_BACKEND": "sqlite",
            # qdrant local 不支持双进程同目录——各实例独立存储（冒烟链路不依赖 RAG）
            "QDRANT_LOCAL_PATH": f"./data/qdrant-mi-{tag}-{port}",
            # 对齐 CI 口径：确定性编排消除模型抖动
            "ORCHESTRATOR_LLM_ENABLED": "false",
        }
    )
    if offline:
        # 离线档：压掉 .env 里的真实 Key（环境变量优先级高于 .env），材料提取
        # 必然降级 Mock——否则"零 Key 转人工"断言在本地有 Key 机器上必挂
        env["LLM_API_KEY"] = ""
    return subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--port", str(port), "--log-level", "warning",
        ],
        env=env,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="双实例 live 冒烟（跨实例补件恢复）")
    parser.add_argument("--base-url-a", default="http://localhost:8000", help="实例 A 地址")
    parser.add_argument("--base-url-b", default="http://localhost:8001", help="实例 B 地址")
    parser.add_argument("--offline", action="store_true",
                        help="离线兜底档（零 API Key）：断言转人工 + 零 schema_reset")
    parser.add_argument("--boot", action="store_true",
                        help="本地自起两个实例（默认端口 8010/8011），退出时自动回收")
    parser.add_argument("--port-a", type=int, default=8010, help="boot 模式实例 A 端口")
    parser.add_argument("--port-b", type=int, default=8011, help="boot 模式实例 B 端口")
    args = parser.parse_args()

    url_a, url_b = args.base_url_a, args.base_url_b
    procs: list[subprocess.Popen] = []
    try:
        if args.boot:
            for port in (args.port_a, args.port_b):
                if _port_in_use(port):
                    print(f"端口 {port} 已被占用（上次 boot 的残留实例？），请先清理再跑",
                          flush=True)
                    sys.exit(1)
            tag = str(int(time.time()))
            print(f"boot：拉起双实例 {args.port_a}/{args.port_b}（sqlite 共享 checkpoint）", flush=True)
            # 顺序拉起：A 就绪再起 B——同时开文件会放大 checkpoint WAL/setup 的
            # 撞锁窗口（服务端已带重试，这里再收窄一层；BGE 预热也不互相抢 CPU）
            proc_a = _boot_instance(args.port_a, tag, offline=args.offline)
            procs = [proc_a]
            url_a = f"http://localhost:{args.port_a}"
            if not _wait_health(url_a):
                print(f"实例 A 未就绪：{url_a}/health", flush=True)
                sys.exit(1)
            print(f"实例 A 就绪：{url_a}", flush=True)
            procs.append(_boot_instance(args.port_b, tag, offline=args.offline))
            url_b = f"http://localhost:{args.port_b}"
            if not _wait_health(url_b):
                print(f"实例 B 未就绪：{url_b}/health", flush=True)
                sys.exit(1)
            print(f"实例 B 就绪：{url_b}", flush=True)

        for url in (url_a, url_b):
            try:
                with urllib.request.urlopen(f"{url}/health", timeout=5) as resp:
                    assert resp.status == 200
            except Exception as e:  # noqa: BLE001 —— 不可达直接失败
                print(f"实例不可达（{url}/health）：{e}", flush=True)
                sys.exit(1)

        print(f"模式：{'离线兜底档（零 API Key）' if args.offline else '完整档（真实 LLM）'} "
              f"| A={url_a} B={url_b}", flush=True)
        asyncio.run(verify(url_a, url_b, offline=args.offline))
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:  # noqa: PERF203 —— 兜底强杀
                proc.kill()
        if procs:
            print("boot：双实例已回收", flush=True)


if __name__ == "__main__":
    main()
