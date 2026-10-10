"""审计事件流回放断言（T181，D086）。

沿 case_events（append-only，(case_id,seq) 唯一）× case_jobs 交付回执（D047）×
decision_documents 版本的联合视图重放断言五不变量：

1. seq 完整性——1..n 连续无空洞无重号（append-only 的读时证明，事后删改露馅；
   重号使事件顺序不可信，顺序类断言 INV2/INV3 对该案跳过）
2. 合规前置——auto_issued 前最后一次 compliance_gate 裁决必 PASS 且金额一致
   （schema_reset 全新重跑后须在重跑段内重新证明）
3. 路由预算——段内 calls 严格 +1 起于 1（段 = schema_reset 切分的重跑边界，
   版本门卫全新重跑合法归零计数器）；mode=llm ⇒ calls ≤ ROUTING_CALL_BUDGET；
   over_budget 后段内永不再 llm（超预算永久降级）
4. 回执单源——补件上传轮次 ≤ 补件回执数（轮 = 连续 material_upload 事件，
   同轮多文件批量补一笔回执；轮次配对而非时序比较——补件上传仅在
   supplement_pending 态被接受、该态仅由 interrupted 补件回执产生，轮次即因果；
   时序不可比：event.created_at 为 server_default UTC 而 job.finished_at 为
   Python 本地时区，历史库两种约定并存，dev 库 316 案实测时序比较全量误报）；
   存在坐席签发版决定书 ⇒ 必有 interrupted 回执（挂起才可恢复）
5. 死信一致——job_failed 事件 ⇒ 该案最新 job status=dead

切面（同 T174 badcase_export 测试缝）：check_invariants 纯函数零 IO 可全矩阵
单测；audit_case / audit_all 异步取数薄层（session 注入）。全程只读。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from schemas.case import CaseStatus
from schemas.contract import ROUTING_CALL_BUDGET
from services.db.models import CaseEvent, CaseJob, DecisionDocument

# 不变量标识（报告与测试共用）
INV_SEQ = "seq_integrity"
INV_COMPLIANCE = "compliance_before_auto"
INV_BUDGET = "routing_budget"
INV_RECEIPT = "receipt_before_resume"
INV_DEAD = "deadletter_consistency"

INV_DESCRIPTIONS: dict[str, str] = {
    INV_SEQ: "事件 seq 应从 1 起连续无空洞（append-only 完整性）",
    INV_COMPLIANCE: "auto 签发前最后一次合规门裁决应为 PASS 且金额一致",
    INV_BUDGET: "路由调用应单调递增且 LLM 调用不超预算，超预算后永久降级",
    INV_RECEIPT: "恢复动作（补件上传/坐席签发）前应有挂起交付回执（D047）",
    INV_DEAD: "job_failed 事件应与最新 job 的 dead 终态互证一致",
}

# 纯函数输入的平铺数据形态（payload 为 JSON 列，结构由发射方保证——
# 本工具正是要断言"结构被保证"，缺字段按违规报告而非猜测）
EventRow = dict[str, Any]
JobRow = dict[str, Any]
DocRow = dict[str, Any]


@dataclass(frozen=True)
class Violation:
    """一条不变量违规（locator 定位到事件流位置或表行）。"""

    case_id: str
    inv: str
    locator: str
    detail: str


@dataclass
class CaseAudit:
    """单案回放结果。"""

    case_id: str
    violations: list[Violation]
    event_count: int
    job_count: int
    doc_count: int

    @property
    def passed(self) -> bool:
        return not self.violations


def check_invariants(
    case_id: str,
    events: list[EventRow],
    jobs: list[JobRow],
    decisions: list[DocRow],
) -> list[Violation]:
    """对单案的联合视图断言五不变量（纯函数，零 IO）。

    events 按 seq 升序；jobs 按 id 升序；decisions 任意序（只用存在性与 issued_by）。
    """

    def v(inv: str, locator: str, detail: str) -> Violation:
        return Violation(case_id=case_id, inv=inv, locator=locator, detail=detail)

    out: list[Violation] = []

    # ---- INV1 seq 完整性 ----
    if not events:
        return [v(INV_SEQ, "events=0", "案件无审计事件流（图未运行或事件被清）")]
    seqs = [int(e["seq"]) for e in events]
    seq_ordered = seqs == list(range(1, len(seqs) + 1))
    has_duplicate = len(set(seqs)) != len(seqs)
    if not seq_ordered:
        holes = sorted(set(range(1, len(seqs) + 1)) - set(seqs))[:5]
        hint = "（存在重号，顺序类断言跳过——事件顺序不可信）" if has_duplicate else ""
        out.append(
            v(
                INV_SEQ,
                f"seq={seqs[0]}..{seqs[-1]}",
                f"seq 序列非 1..{len(seqs)} 连续（空洞/重复：{holes}）{hint}",
            )
        )

    # ---- INV2 合规前置 / INV3 路由预算 ----
    # 仅在 seq 无重号时断言（重号使 ORDER BY seq 的事件顺序不可信，断言只会产噪声）；
    # 纯空洞不影响剩余事件的相对顺序，仍可断言。schema_reset = 段边界（版本门卫
    # 全新重跑合法归零路由计数器），重跑段内须重新证明合规前置。
    last_verdict: str | None = None
    last_amount_ok: bool | None = None
    expected_call = 1
    over_budget_seen = False
    for e in events if not has_duplicate else ():
        payload = e.get("payload") or {}
        if e["kind"] == "schema_reset":
            # 版本门卫全新重跑：计数器与合规证明均按新段重新起算
            last_verdict = None
            last_amount_ok = None
            expected_call = 1
            over_budget_seen = False
            continue
        if e["kind"] == "stage_result" and e.get("stage") == "compliance_gate":
            verdict = payload.get("verdict")
            if verdict not in ("PASS", "MODIFY", "REJECT"):
                out.append(
                    v(
                        INV_COMPLIANCE,
                        f"seq={e['seq']}",
                        f"合规裁决 verdict 缺失或非法：{verdict!r}",
                    )
                )
                continue
            last_verdict = str(verdict)
            # amount_consistent 缺省按模型默认 True（ComplianceOutput 默认值）
            last_amount_ok = bool(payload.get("amount_consistent", True))
        elif e["kind"] == "status_change" and payload.get("status") == CaseStatus.AUTO_ISSUED:
            if last_verdict != "PASS" or last_amount_ok is not True:
                got = f"{last_verdict}/{'' if last_amount_ok is None else last_amount_ok}"
                out.append(
                    v(
                        INV_COMPLIANCE,
                        f"seq={e['seq']}",
                        f"auto_issued 前最后一次合规裁决为 {got}（应 PASS/True）",
                    )
                )
        if e["kind"] != "routing":
            continue
        calls = payload.get("calls")
        mode = str(payload.get("mode") or "")
        if not isinstance(calls, int) or calls != expected_call:
            out.append(
                v(
                    INV_BUDGET,
                    f"seq={e['seq']}",
                    f"routing.calls={calls!r}，应段内严格递增（期望 {expected_call}）",
                )
            )
            expected_call = calls + 1 if isinstance(calls, int) else expected_call + 1
        else:
            expected_call += 1
        if mode == "llm":
            if isinstance(calls, int) and calls > ROUTING_CALL_BUDGET:
                out.append(
                    v(
                        INV_BUDGET,
                        f"seq={e['seq']}",
                        f"LLM 路由调用 #{calls} 超预算 {ROUTING_CALL_BUDGET}",
                    )
                )
            if over_budget_seen:
                out.append(
                    v(INV_BUDGET, f"seq={e['seq']}", "超预算后再次出现 LLM 路由（应永久降级）")
                )
        if payload.get("over_budget") is True:
            over_budget_seen = True

    # ---- INV4 回执单源（轮次配对——同轮多文件只须一笔回执；时序不可比：
    # 事件 UTC / job 本地时区两种约定并存，见模块注释） ----
    supplement_receipt_count = sum(
        1 for j in jobs
        if j.get("outcome") == "interrupted"
        and (j.get("interrupt_payload") or {}).get("kind") == "supplement"
    )
    upload_rounds = 0
    prev_was_upload = False
    for e in events:
        if e["kind"] == "material_upload":
            if not prev_was_upload:
                upload_rounds += 1  # 连续上传事件（无夹杂图事件）计为同一轮
            prev_was_upload = True
        else:
            prev_was_upload = False
    if upload_rounds > supplement_receipt_count:
        out.append(
            v(
                INV_RECEIPT,
                f"material_upload_rounds×{upload_rounds}",
                f"补件上传 {upload_rounds} 轮但补件回执仅 {supplement_receipt_count} 个"
                "（无回执的恢复轮）",
            )
        )
    if any(str(d.get("issued_by") or "").startswith("agent:") for d in decisions):
        if not any(j.get("outcome") == "interrupted" for j in jobs):
            out.append(
                v(
                    INV_RECEIPT,
                    "decisions",
                    "存在坐席签发版决定书但无任何 interrupted 回执（未经挂起的签发）",
                )
            )

    # ---- INV5 死信一致 ----
    if any(e["kind"] == "job_failed" for e in events):
        latest = jobs[-1] if jobs else None
        if latest is None or latest.get("status") != "dead":
            got = "无 job" if latest is None else f"status={latest.get('status')!r}"
            out.append(
                v(INV_DEAD, "latest_job", f"job_failed 事件后最新 job {got}（应 dead）")
            )

    return out


# ===== 取数薄层（session 注入；行 → 平铺 dict 只取断言所需字段） =====


def _event_row(row: CaseEvent) -> EventRow:
    return {
        "seq": row.seq,
        "kind": row.kind,
        "stage": row.stage,
        "payload": row.payload,
        "created_at": row.created_at,
    }


def _job_row(row: CaseJob) -> JobRow:
    return {
        "id": row.id,
        "status": row.status,
        "outcome": row.outcome,
        "interrupt_payload": row.interrupt_payload,
    }


def _doc_row(row: DecisionDocument) -> DocRow:
    return {"version": row.version, "issued_by": row.issued_by}


async def audit_case(session: AsyncSession, case_id: str) -> CaseAudit:
    """单案回放：取该案三源数据 → 纯函数断言。"""
    events = (
        (await session.execute(
            select(CaseEvent).where(CaseEvent.case_id == case_id).order_by(CaseEvent.seq)
        )).scalars().all()
    )
    jobs = (
        (await session.execute(
            select(CaseJob).where(CaseJob.case_id == case_id).order_by(CaseJob.id)
        )).scalars().all()
    )
    docs = (
        (await session.execute(
            select(DecisionDocument).where(DecisionDocument.case_id == case_id)
        )).scalars().all()
    )
    event_rows = [_event_row(r) for r in events]
    violations = check_invariants(
        case_id, event_rows, [_job_row(r) for r in jobs], [_doc_row(r) for r in docs]
    )
    return CaseAudit(
        case_id=case_id,
        violations=violations,
        event_count=len(event_rows),
        job_count=len(jobs),
        doc_count=len(docs),
    )


@dataclass
class BatchAudit:
    """批跑结果（--all）。"""

    cases: list[CaseAudit]
    generated_at: dt.datetime

    @property
    def violation_count(self) -> int:
        return sum(len(c.violations) for c in self.cases)

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self.cases if not c.passed)

    def by_inv(self) -> dict[str, int]:
        stat: dict[str, int] = {inv: 0 for inv in INV_DESCRIPTIONS}
        for c in self.cases:
            for violation in c.violations:
                stat[violation.inv] = stat.get(violation.inv, 0) + 1
        return stat


async def audit_all(session: AsyncSession, limit: int | None = None) -> BatchAudit:
    """全量批跑：case_events 去重案件号（字典序），三源各一次批量查询后分组。"""
    case_ids = list(
        (await session.execute(
            select(CaseEvent.case_id).distinct().order_by(CaseEvent.case_id).limit(limit)
        )).scalars()
    )
    cases: list[CaseAudit] = []
    if case_ids:
        event_rows = (
            (await session.execute(
                select(CaseEvent).where(CaseEvent.case_id.in_(case_ids))
                .order_by(CaseEvent.case_id, CaseEvent.seq)
            )).scalars()
        )
        events_by_case: dict[str, list[EventRow]] = {cid: [] for cid in case_ids}
        for row in event_rows:
            events_by_case[row.case_id].append(_event_row(row))
        job_rows = (
            (await session.execute(
                select(CaseJob).where(CaseJob.case_id.in_(case_ids))
                .order_by(CaseJob.case_id, CaseJob.id)
            )).scalars()
        )
        jobs_by_case: dict[str, list[JobRow]] = {cid: [] for cid in case_ids}
        for row in job_rows:
            jobs_by_case[row.case_id].append(_job_row(row))
        doc_rows = (
            (await session.execute(
                select(DecisionDocument).where(DecisionDocument.case_id.in_(case_ids))
            )).scalars()
        )
        docs_by_case: dict[str, list[DocRow]] = {cid: [] for cid in case_ids}
        for row in doc_rows:
            docs_by_case[row.case_id].append(_doc_row(row))
        for cid in case_ids:
            violations = check_invariants(
                cid, events_by_case[cid], jobs_by_case[cid], docs_by_case[cid]
            )
            cases.append(
                CaseAudit(
                    case_id=cid,
                    violations=violations,
                    event_count=len(events_by_case[cid]),
                    job_count=len(jobs_by_case[cid]),
                    doc_count=len(docs_by_case[cid]),
                )
            )
    return BatchAudit(cases=cases, generated_at=dt.datetime.now())
