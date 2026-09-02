"""评测 API（T051，D027）：一键启动评测（子进程隔离）+ 进度查询 + 报告查看。

路由只做参数校验、生命周期映射与 IO 编排，运行管理在 services/eval_runner.py：
- POST /runs          启动评测（单活跃守卫，并发 409）
- GET  /runs          运行记录列表
- GET  /runs/{id}     运行状态/进度/日志尾（UI 轮询）
- GET  /reports       报告列表（evals/reports/*.json，新→旧）
- GET  /reports/{name} 报告详情（汇总/轨迹/失败明细）
- GET  /meta          表单可选项（数据集/分类/变体）
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, HTTPException

from schemas.api import (
    EvalMetaResponse,
    EvalReportBrief,
    EvalReportListResponse,
    EvalRunBrief,
    EvalRunListResponse,
    EvalRunStartRequest,
    EvalRunStartResponse,
    EvalRunStatusResponse,
    EvalTrendPoint,
    EvalTrendsResponse,
    EvalVariantInfo,
)
from services.eval_runner import EvalRun, EvalRunParams, RunAlreadyActiveError, get_eval_runner

router = APIRouter(prefix="/api/v1/evals", tags=["evals"])

# 报告文件名白名单（防路径穿越：只允许纯文件名）
_REPORT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.json$")


def _brief(run: EvalRun) -> EvalRunBrief:
    return EvalRunBrief(
        run_id=run.run_id,
        status=run.status,
        created_at=run.created_at,
        finished_at=run.finished_at,
        params=EvalRunStartRequest(**run.params.__dict__),
        source="ui",
        git_sha=run.git_sha,
    )


def _brief_from_row(row: Any) -> EvalRunBrief:
    """eval_runs 历史行 → 运行摘要（T052：服务重启后历史可查）。"""
    return EvalRunBrief(
        run_id=row.run_id,
        status=row.status,
        created_at=_fmt_dt(row.created_at),
        finished_at=_fmt_dt(row.finished_at),
        params=EvalRunStartRequest(
            dataset=row.dataset,
            category=row.category,
            limit=row.run_limit,
            variant=row.variant,
        ),
        source=row.source,
        git_sha=row.git_sha,
        task_completion_rate=float(row.task_completion_rate)
        if row.task_completion_rate is not None
        else None,
        tool_accuracy=float(row.tool_accuracy) if row.tool_accuracy is not None else None,
    )


def _status_from_row(row: Any) -> EvalRunStatusResponse:
    """eval_runs 历史行 → 运行状态响应（进度取落库快照，日志尾按行拆分）。"""
    return EvalRunStatusResponse(
        run_id=row.run_id,
        status=row.status,
        created_at=_fmt_dt(row.created_at),
        finished_at=_fmt_dt(row.finished_at),
        params=EvalRunStartRequest(
            dataset=row.dataset,
            category=row.category,
            limit=row.run_limit,
            variant=row.variant,
        ),
        current=row.total,  # 终态行无 current 列：跑完即全部处理
        total=row.total,
        passed=row.passed,
        failed=row.failed,
        return_code=row.return_code,
        git_sha=row.git_sha,
        source=row.source,
        task_completion_rate=float(row.task_completion_rate)
        if row.task_completion_rate is not None
        else None,
        tool_accuracy=float(row.tool_accuracy) if row.tool_accuracy is not None else None,
        report_name=row.report_name,
        log_tail=(row.log_tail or "").splitlines(),
    )


def _fmt_dt(value: Any) -> str | None:
    """DB 时间 → 显示字符串（SQLite/PG 均可能返回 datetime 或 str）。"""
    if value is None:
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


@router.post("/runs", response_model=EvalRunStartResponse)
async def start_run(body: EvalRunStartRequest) -> EvalRunStartResponse:
    """启动一次评测（子进程执行，立即返回 run_id；进度经 GET /runs/{id} 轮询）。"""
    try:
        run = await get_eval_runner().start_run(EvalRunParams(**body.model_dump()))
    except RunAlreadyActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return EvalRunStartResponse(run=_brief(run))


@router.get("/runs", response_model=EvalRunListResponse)
async def list_runs() -> EvalRunListResponse:
    """运行历史：DB 落库行为主（T052，重启后可查），内存运行优先（实时进度）。"""
    from services.eval_history import list_runs as list_history

    merged: dict[str, EvalRunBrief] = {}
    for row in await list_history(50):
        merged[row.run_id] = _brief_from_row(row)
    for run in get_eval_runner().list_runs():
        # running 内存覆盖（实时进度）；终态以 DB 行为准（带率值），DB 缺行时才用内存兜底
        if run.status == "running" or run.run_id not in merged:
            merged[run.run_id] = _brief(run)
    items = sorted(merged.values(), key=lambda b: b.created_at, reverse=True)
    return EvalRunListResponse(runs=items[:50])


@router.get("/runs/{run_id}", response_model=EvalRunStatusResponse)
async def get_run(run_id: str) -> EvalRunStatusResponse:
    run = get_eval_runner().get_run(run_id)
    row = await get_history_run(run_id)
    # 终态优先取 DB 行（带率值/summary）；running 取内存（实时进度）；两者互补兜底
    if row is not None and (run is None or run.status != "running"):
        return _status_from_row(row)
    if run is not None:
        return EvalRunStatusResponse(
            run_id=run.run_id,
            status=run.status,
            created_at=run.created_at,
            finished_at=run.finished_at,
            params=EvalRunStartRequest(**run.params.__dict__),
            current=run.current,
            total=run.total,
            passed=run.passed,
            failed=run.failed,
            return_code=run.return_code,
            git_sha=run.git_sha,
            source="ui",
            report_name=run.report_name,
            log_tail=list(run.log_tail),
        )
    row = await get_history_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"运行不存在：{run_id}")
    return _status_from_row(row)


async def get_history_run(run_id: str) -> Any:
    """历史行查询（独立函数便于测试 patch）。"""
    from services.eval_history import get_run as get_history

    return await get_history(run_id)


@router.get("/reports", response_model=EvalReportListResponse)
async def list_reports() -> EvalReportListResponse:
    """报告列表（只收 test_suite 口径报告——含 summary 键；A/B 对比报告结构不同不列）。"""
    reports: list[EvalReportBrief] = []
    for path in sorted(
        get_eval_runner().reports_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        data = _read_report(path.name)
        if data is None:
            continue
        s = data.get("summary") or {}
        if not isinstance(s, dict) or "task_completion_rate" not in s:
            continue
        reports.append(
            EvalReportBrief(
                name=path.name,
                generated_at=str(data.get("generated_at", "")),
                dataset=str(data.get("dataset", "")),
                variant=str(data.get("variant", "")),
                category=data.get("category"),
                total=int(s.get("total", 0)),
                passed=int(s.get("passed", 0)),
                task_completion_rate=float(s.get("task_completion_rate", 0.0)),
                tool_accuracy=float(s.get("tool_accuracy", 0.0)),
                compliance_pass_rate=float(s.get("compliance_pass_rate", 0.0)),
                avg_duration_s=float(s.get("avg_duration_s", 0.0)),
            )
        )
    return EvalReportListResponse(reports=reports)


@router.get("/reports/{name}")
async def get_report(name: str) -> dict[str, Any]:
    if not _REPORT_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"非法报告名：{name}")
    data = _read_report(name)
    if data is None:
        raise HTTPException(status_code=404, detail=f"报告不存在或不可读：{name}")
    return data


@router.get("/meta", response_model=EvalMetaResponse)
async def meta() -> EvalMetaResponse:
    """评测表单可选项（惰性导入，路由加载保持轻量）。"""
    from evals.schemas import EvalCategory
    from evals.test_suite import DATASETS
    from evals.variants import VARIANTS

    return EvalMetaResponse(
        datasets=sorted(DATASETS.keys()),
        categories=[c.value for c in EvalCategory],
        variants=[
            EvalVariantInfo(name=v.name, description=v.description) for v in VARIANTS.values()
        ],
    )


@router.get("/trends", response_model=EvalTrendsResponse)
async def trends() -> EvalTrendsResponse:
    """趋势数据（T053，D029）：DB 历史行 + reports 文件双源合并，按 report_name 去重。

    完成态且带率值的运行才有意义；文件源覆盖 T052 之前的存量报告（baseline 等）。
    """
    from services.eval_history import list_runs as list_history

    points: list[EvalTrendPoint] = []
    covered_reports: set[str] = set()

    for row in await list_history(500):
        if row.status != "completed" or row.task_completion_rate is None:
            continue
        if row.report_name:
            covered_reports.add(row.report_name)
        points.append(
            EvalTrendPoint(
                time=_fmt_dt(row.finished_at or row.created_at) or "",
                dataset=row.dataset,
                variant=row.variant,
                task_completion_rate=float(row.task_completion_rate),
                tool_accuracy=float(row.tool_accuracy) if row.tool_accuracy is not None else 0.0,
                passed=row.passed,
                total=row.total,
                git_sha=row.git_sha,
                source="db",
                label=row.run_id,
            )
        )

    for path in sorted(get_eval_runner().reports_dir.glob("*.json")):
        if path.name in covered_reports:
            continue
        data = _read_report(path.name)
        if data is None:
            continue
        s = data.get("summary") or {}
        if not isinstance(s, dict) or "task_completion_rate" not in s:
            continue
        points.append(
            EvalTrendPoint(
                time=str(data.get("generated_at", "")),
                dataset=str(data.get("dataset", "")),
                variant=str(data.get("variant", "")),
                task_completion_rate=float(s.get("task_completion_rate", 0.0)),
                tool_accuracy=float(s.get("tool_accuracy", 0.0)),
                passed=int(s.get("passed", 0)),
                total=int(s.get("total", 0)),
                git_sha=str(data.get("git_sha", "unknown") or "unknown"),
                source="report",
                label=path.name,
            )
        )

    points.sort(key=lambda p: p.time)
    return EvalTrendsResponse(points=points)


def _read_report(name: str) -> dict[str, Any] | None:
    """读报告 JSON；缺失/损坏返回 None（列表接口跳过，详情接口映射 404）。"""
    path = get_eval_runner().reports_dir / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
