"""T092 核赔平台端到端验证（容器内/宿主机均可运行）。

对运行中的后端执行四阶段冒烟：
1. 提交正常案 → 自动签发 + 核定金额 4640.00
2. 查询案件详情 → 审计时间线 + 决定书版本
3. 提交缺件案 → 补件挂起 → 上传补件 → 自动恢复签发
4. 提交未上线险种案 → 受理转人工（escape）

用法：
    uv run python -m scripts.verify_adjudication [--base-url http://localhost:8000]
退出码：0=全通过，1=有失败。
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from decimal import Decimal

import httpx

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} {detail}")


async def verify(base_url: str) -> None:
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as ac:
        # ===== 1. 自动签发主链路 =====
        print("\n--- 1. 自动签发主链路 ---")
        body = {
            "user_id": "verify-auto",
            "policy_no": "POL-2025-0001",
            "claimed_amount": "15800.00",
            "incident_date": "2026-08-10",
            "incident_description": "急性阑尾炎住院手术，共花费15800元。",
            "materials": [
                {"file_name": "invoice.jpg", "doc_type": "invoice"},
                {"file_name": "diagnosis.jpg", "doc_type": "diagnosis"},
                {"file_name": "cost_list.pdf", "doc_type": "cost_list"},
            ],
        }
        resp = await ac.post("/api/v1/cases", json=body)
        check("提交返回 201", resp.status_code == 201, f"got {resp.status_code}")
        data = resp.json()
        case_id = data.get("case_id", "")
        check("案件状态 auto_issued", data.get("status") == "auto_issued")
        check("final_decision approved", data.get("final_decision") == "approved")
        approved = str(data.get("approved_amount") or "")
        check(f"核定金额 4640.00（实际 {approved}）",
              approved and Decimal(approved) == Decimal("4640.00"))
        doc = data.get("decision_document")
        check("决定书存在", doc is not None and bool(doc.get("body")))

        # ===== 2. 案件详情 =====
        print("\n--- 2. 案件详情 ---")
        resp = await ac.get(f"/api/v1/cases/{case_id}")
        check("详情返回 200", resp.status_code == 200)
        detail = resp.json()
        check("时间线非空", len(detail.get("timeline", [])) >= 8)
        kinds = {e["kind"] for e in detail.get("timeline", [])}
        check("审计事件种类齐", {"routing", "stage_result", "status_change"} <= kinds)

        # ===== 3. 补件闭环 =====
        print("\n--- 3. 补件闭环 ---")
        supp = {
            **body,
            "user_id": "verify-supp",
            "materials": [
                {"file_name": "invoice.jpg", "doc_type": "invoice"},
                {"file_name": "diagnosis.jpg", "doc_type": "diagnosis"},
            ],
        }
        resp = await ac.post("/api/v1/cases", json=supp)
        supp_id = resp.json().get("case_id", "")
        check("缺件案补件挂起", resp.json().get("status") == "supplement_pending")

        # 上传缺失材料
        resp = await ac.post(
            f"/api/v1/cases/{supp_id}/materials",
            files={"file": ("cost_list.pdf", b"%PDF-1.4 fake", "application/pdf")},
            data={"doc_type": "cost_list"},
        )
        check("上传 200", resp.status_code == 200)
        check("自动恢复 auto_issued",
              resp.json().get("case_status") == "auto_issued")

        detail = (await ac.get(f"/api/v1/cases/{supp_id}")).json()
        check("补件后核定 4640.00",
              Decimal(str(detail.get("approved_amount") or "0")) == Decimal("4640.00"))

        # ===== 4. 未上线险种转人工 =====
        print("\n--- 4. 未上线险种 ---")
        offline = {
            "user_id": "verify-offline",
            "policy_no": "POL-2023-0004",
            "claimed_amount": "8600.00",
            "incident_date": "2026-08-25",
            "incident_description": "雨天摔倒骨折，费用8600元。",
            "declared_case_type": "accident",
            "materials": [],
        }
        resp = await ac.post("/api/v1/cases", json=offline)
        check("受理转人工", resp.json().get("human", {}).get("kind") == "escape")

        # ===== 5. 工单列表 =====
        print("\n--- 5. 工单列表 ---")
        resp = await ac.get("/api/v1/interventions/cases")
        check("工单列表 200", resp.status_code == 200)
        check("工单含转人工案件", resp.json().get("total", 0) >= 1)

    print(f"\n{'=' * 40}")
    print(f"端到端验证：{passed} 通过 / {failed} 失败")
    print(f"{'=' * 40}")
    if failed > 0:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="核赔平台端到端验证")
    parser.add_argument("--base-url", default="http://localhost:8000", help="后端地址")
    args = parser.parse_args()

    # 健康检查
    try:
        resp = urllib.request.urlopen(f"{args.base_url}/health", timeout=5)
        assert resp.status == 200
    except Exception as e:
        print(f"后端不可达（{args.base_url}/health）：{e}")
        sys.exit(1)

    asyncio.run(verify(args.base_url))


import asyncio  # noqa: E402

if __name__ == "__main__":
    main()
