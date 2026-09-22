"""T092 核赔平台端到端验证（容器内/宿主机均可运行）。

对运行中的后端执行五组冒烟：
1. 自动签发主链路：提交（无材料）→ 补件挂起 → 上传三份 Word 材料 → 自动签发 4640.00
2. 案件详情：审计时间线 + 决定书
3. 补件闭环：缺件案补件挂起 → 上传补件后恢复流转
4. 未上线险种（重疾险 → unknown）→ 受理转人工（escape）
5. 工单列表

口径说明（T140 置信度校准后）：材料只声明 file_name/doc_type 而无真实文件时走
引用型兜底，关键字段（金额/诊断）缺失会扣分压低置信度 → 低于
material_confidence_floor(0.6) → 转人工裁量，这是设计行为而非缺陷。因此
自动签发路径必须上传真实可提取的文件——本脚本用 python-docx 现场生成 Word
（走 text_model 提取，来源基准 0.9），不依赖 vision/扫描件。

用法：
    uv run python -m scripts.verify_adjudication [--base-url http://localhost:8000]
退出码：0=全通过，1=有失败。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
import time
import urllib.request
from decimal import Decimal

import httpx

passed = 0
failed = 0


# 运行态（图在跑）/ 挂起态（等补件或人工）/ 终态（auto_issued、referred、closed）
RUNNING = {"received", "in_progress"}
PENDING = {"supplement_pending"}
TERMINAL = {"auto_issued", "referred", "closed"}
_JOB_ACTIVE = {"queued", "running"}

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# 医疗险必需材料三件（与 schemas.lines MEDICAL_PACK.required_docs 同口径）
_MATERIAL_TEXTS: dict[str, list[str]] = {
    "invoice": [
        "医疗费用发票",
        "患者姓名：张伟",
        "诊断：急性阑尾炎",
        "金额：15800 元",
        "日期：2026-08-10",
    ],
    "diagnosis": [
        "诊断证明",
        "患者张伟，确诊急性阑尾炎，于 2026-08-10 住院手术治疗",
        "住院费用合计 15800 元",
    ],
    "cost_list": [
        "住院费用清单",
        "姓名：张伟",
        "项目：急性阑尾炎手术及住院治疗",
        "总金额：15800 元",
        "日期：2026-08-10",
    ],
}


def _make_docx_bytes(lines: list[str]) -> bytes:
    """现场生成 Word 材料（python-docx 写内存流）：中文文本 → text_model 提取路径。"""
    import docx

    document = docx.Document()
    for line in lines:
        document.add_paragraph(line)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


async def wait_settled(ac: httpx.AsyncClient, case_id: str, timeout_s: float = 180) -> dict:
    """轮询案件详情至"停稳"（非 received/in_progress）：挂起态或终态均返回。"""
    detail: dict = {}
    for _ in range(int(timeout_s / 0.5)):
        detail = (await ac.get(f"/api/v1/cases/{case_id}")).json()
        if detail.get("status") not in RUNNING:
            return detail
        await asyncio.sleep(0.5)
    return detail


async def wait_job_done(ac: httpx.AsyncClient, case_id: str, timeout_s: float = 180) -> dict:
    """轮询至最近一个交付任务不再活跃（T103 background 档：避免连击上传撞车）。"""
    detail: dict = {}
    for _ in range(int(timeout_s / 0.5)):
        detail = (await ac.get(f"/api/v1/cases/{case_id}")).json()
        job = detail.get("job") or {}
        if job.get("status") and job["status"] not in _JOB_ACTIVE:
            return detail
        if detail.get("status") in TERMINAL:
            return detail
        await asyncio.sleep(0.5)
    return detail


async def wait_terminal(ac: httpx.AsyncClient, case_id: str, timeout_s: float = 180) -> dict:
    """轮询至终态（既不在跑也不挂起）。"""
    detail: dict = {}
    for _ in range(int(timeout_s / 0.5)):
        detail = (await ac.get(f"/api/v1/cases/{case_id}")).json()
        if detail.get("status") in TERMINAL:
            return detail
        await asyncio.sleep(0.5)
    return detail


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} {detail}")


async def upload_material(
    ac: httpx.AsyncClient, case_id: str, doc_type: str
) -> tuple[int, dict]:
    """上传一份生成的 Word 材料，返回 (状态码, 响应体)。"""
    resp = await ac.post(
        f"/api/v1/cases/{case_id}/materials",
        files={
            "file": (
                f"{doc_type}.docx",
                _make_docx_bytes(_MATERIAL_TEXTS[doc_type]),
                _DOCX_MIME,
            )
        },
        data={"doc_type": doc_type},
    )
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, body


async def verify(base_url: str, offline: bool = False) -> None:
    # 幂等键含 user_id：加时间戳后缀，保证脚本可重复运行（否则二次运行命中 200）
    tag = str(int(time.time()))
    async with httpx.AsyncClient(base_url=base_url, timeout=60) as ac:
        # ===== 1. 自动签发主链路（真实材料上传补齐）=====
        print(f"\n--- 1. 自动签发主链路{'（离线兜底档：只守落档与状态机）' if offline else ''} ---")
        body = {
            "user_id": f"verify-auto-{tag}",
            "policy_no": "POL-2025-0001",
            "claimed_amount": "15800.00",
            "incident_date": "2026-08-10",
            "incident_description": "急性阑尾炎住院手术，共花费15800元。",
            "materials": [],  # 零材料提交 → 补件挂起 → 逐份上传补齐（唯一能拿到真实提取的路径）
        }
        resp = await ac.post("/api/v1/cases", json=body)
        check("提交返回 201", resp.status_code == 201, f"got {resp.status_code}")
        data = resp.json()
        case_id = data.get("case_id", "")
        check("交付凭证存在", data.get("job") is not None)

        detail = await wait_settled(ac, case_id)
        check("零材料提交挂起补件", detail.get("status") == "supplement_pending",
              f"got {detail.get('status')}")

        sources: dict[str, str] = {}
        for doc_type in _MATERIAL_TEXTS:
            if detail.get("status") != "supplement_pending":
                break  # 已进终态，无需继续补件
            code, up = await upload_material(ac, case_id, doc_type)
            check(f"上传 {doc_type}.docx 200", code == 200, f"got {code}")
            sources[doc_type] = str(up.get("source") or "")
            # 等上一份的 resume 任务结束再传下一份（background 档下连击会撞活跃任务）
            await wait_job_done(ac, case_id)
            detail = await wait_settled(ac, case_id)
        if offline:
            # 零 Key：提取必然降级 mock_fallback（基准 0.3 + 字段扣分）→ 低于 floor
            # 0.6 → 按设计转人工。门禁只守"上传链路与状态机"，不守签发结论
            check("三份材料均降级 Mock 提取（零 Key 预期）",
                  all(s == "mock_fallback" for s in sources.values()), f"sources={sources}")
        else:
            check("三份材料均走真实提取（text_model）",
                  all(s == "text_model" for s in sources.values()), f"sources={sources}")

        detail = await wait_terminal(ac, case_id)
        if offline:
            # 离线档的终态即"材料置信度不足 → 转人工裁量"（T140 设计行为）
            check("案件进终态", detail.get("status") in TERMINAL,
                  f"got {detail.get('status')}")
            check("零 Key 转人工裁量（置信度不足）", detail.get("status") == "referred",
                  f"got {detail.get('status')} / {detail.get('human')}")
            check("材料落档三份", len(detail.get("materials", [])) == 3,
                  f"got {len(detail.get('materials', []))}")
        else:
            check("案件状态 auto_issued", detail.get("status") == "auto_issued",
                  f"got {detail.get('status')} / {detail.get('human')}")
            check("final_decision approved", detail.get("final_decision") == "approved")
            approved = str(detail.get("approved_amount") or "")
            check(f"核定金额 4640.00（实际 {approved}）",
                  approved and Decimal(approved) == Decimal("4640.00"))
            doc = detail.get("decision_document")
            check("决定书存在", doc is not None and bool(doc.get("body")))

        # ===== 2. 案件详情 =====
        print("\n--- 2. 案件详情 ---")
        resp = await ac.get(f"/api/v1/cases/{case_id}")
        check("详情返回 200", resp.status_code == 200)
        detail = resp.json()
        check("时间线非空", len(detail.get("timeline", [])) >= 8,
              f"got {len(detail.get('timeline', []))}")
        kinds = {e["kind"] for e in detail.get("timeline", [])}
        # status_change 由 auto_adjudicate 在签发/终态落库时写；离线档案件停在
        # human_gate 挂起（interrupt），故只要求 routing + stage_result
        expected_kinds = {"routing", "stage_result"}
        if not offline:
            expected_kinds.add("status_change")
        check("审计事件种类齐", expected_kinds <= kinds, f"kinds={sorted(kinds)}")
        check("含材料上传审计", "material_upload" in kinds)

        # ===== 3. 补件闭环 =====
        print("\n--- 3. 补件闭环 ---")
        supp = {
            **body,
            "user_id": f"verify-supp-{tag}",
            "materials": [
                {"file_name": "invoice.docx", "doc_type": "invoice"},
                {"file_name": "diagnosis.docx", "doc_type": "diagnosis"},
            ],
        }
        resp = await ac.post("/api/v1/cases", json=supp)
        supp_id = resp.json().get("case_id", "")
        supp_detail = await wait_settled(ac, supp_id)
        check("缺件案补件挂起", supp_detail.get("status") == "supplement_pending",
              f"got {supp_detail.get('status')}")
        check("缺件提示含费用清单",
              "费用清单" in str(supp_detail.get("human", {}).get("missing")),
              f"got {supp_detail.get('human')}")

        code, _ = await upload_material(ac, supp_id, "cost_list")
        check("上传 200", code == 200, f"got {code}")
        supp_detail = await wait_terminal(ac, supp_id)
        # 另两份仅声明无文件 → 引用型兜底字段缺失 → 转人工裁量（T140 设计行为）：
        # 闭环的验收点是"恢复流转且进入终态"，而非必然自动签发
        check("补件后进终态（脱离挂起）",
              supp_detail.get("status") in TERMINAL, f"got {supp_detail.get('status')}")

        # ===== 4. 未上线险种转人工 =====
        print("\n--- 4. 未上线险种 ---")
        offline_case = {
            "user_id": f"verify-offline-{tag}",
            "policy_no": "POL-2025-0002",  # 重疾险：不在任何险种 pack → unknown
            "claimed_amount": "8600.00",
            "incident_date": "2026-08-25",
            "incident_description": "确诊恶性肿瘤，申请重疾赔付。",
            "materials": [],
        }
        resp = await ac.post("/api/v1/cases", json=offline_case)
        offline_detail = await wait_settled(ac, resp.json().get("case_id", ""))
        check("受理转人工（escape）",
              offline_detail.get("human", {}).get("kind") == "escape",
              f"got {offline_detail.get('status')} / {offline_detail.get('human')}")
        check("险种归类 unknown", offline_detail.get("case_type") == "unknown",
              f"got {offline_detail.get('case_type')}")

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
    parser.add_argument(
        "--offline",
        action="store_true",
        help="离线兜底档（零 API Key）：材料提取必然降级 Mock（0.3 < floor 0.6）→ "
             "按设计转人工；只断言受理/上传落档/补件闭环/状态机/审计/escape/工单，"
             "不断言自动签发与核定金额（那部分由零 LLM 的评测门 coverage）",
    )
    args = parser.parse_args()

    # 健康检查
    try:
        resp = urllib.request.urlopen(f"{args.base_url}/health", timeout=5)
        assert resp.status == 200
    except Exception as e:
        print(f"后端不可达（{args.base_url}/health）：{e}")
        sys.exit(1)

    print(f"模式：{'离线兜底档（零 API Key）' if args.offline else '完整档（真实 LLM）'}")
    asyncio.run(verify(args.base_url, offline=args.offline))



if __name__ == "__main__":
    main()
