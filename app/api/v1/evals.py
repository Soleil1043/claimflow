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
    )


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
    return EvalRunListResponse(runs=[_brief(r) for r in get_eval_runner().list_runs()])


@router.get("/runs/{run_id}", response_model=EvalRunStatusResponse)
async def get_run(run_id: str) -> EvalRunStatusResponse:
    run = get_eval_runner().get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"运行不存在：{run_id}")
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
        report_name=run.report_name,
        log_tail=list(run.log_tail),
    )


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


def _read_report(name: str) -> dict[str, Any] | None:
    """读报告 JSON；缺失/损坏返回 None（列表接口跳过，详情接口映射 404）。"""
    path = get_eval_runner().reports_dir / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
