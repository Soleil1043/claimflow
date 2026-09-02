"""评测台界面（T051，D027）：一键启动评测 + 实时进度 + 结果查看。

架构与聊天演示（ui/app.py）一致：界面走 HTTP 调 FastAPI 后端（/api/v1/evals/*），
评测由后端子进程执行（D027），前端 gr.Timer 每 2s 轮询进度，结束后自动加载报告。

启动：
    uv run uvicorn app.main:app --port 8000   # 先起后端
    uv run python ui/eval_app.py              # 再起评测台（默认 7861）
"""

from __future__ import annotations

import os

import gradio as gr
import httpx

API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
POLL_SECONDS = 2.0
LOG_LINES_SHOWN = 30


class EvalClient:
    """评测 API 客户端：启动/轮询/报告查询。"""

    def __init__(self, base_url: str) -> None:
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=60)

    async def meta(self) -> dict:
        resp = await self._http.get("/api/v1/evals/meta")
        resp.raise_for_status()
        return resp.json()

    async def start_run(self, payload: dict) -> dict:
        resp = await self._http.post("/api/v1/evals/runs", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def status(self, run_id: str) -> dict:
        resp = await self._http.get(f"/api/v1/evals/runs/{run_id}")
        resp.raise_for_status()
        return resp.json()

    async def reports(self) -> list[dict]:
        resp = await self._http.get("/api/v1/evals/reports")
        resp.raise_for_status()
        return resp.json()["reports"]

    async def trends(self) -> dict:
        resp = await self._http.get("/api/v1/evals/trends")
        resp.raise_for_status()
        return resp.json()

    async def report(self, name: str) -> dict:
        resp = await self._http.get(f"/api/v1/evals/reports/{name}")
        resp.raise_for_status()
        return resp.json()


client = EvalClient(API_BASE)


def _status_md(run: dict) -> str:
    """运行状态 → 进度文本（文本进度条 + 通过/失败计数）。"""
    current, total = run["current"], run["total"]
    pct = f"{current / total:.0%}" if total else "—"
    bar = (
        "█" * int(current / total * 20) + "░" * (20 - int(current / total * 20))
        if total
        else "░" * 20
    )
    icon = {"running": "🟢", "completed": "✅", "failed": "❌"}.get(run["status"], "⚪")
    label = {"running": "运行中", "completed": "已完成", "failed": "失败"}.get(
        run["status"], run["status"]
    )
    p = run.get("params", {})
    scope = (
        p.get("dataset", "")
        + (f"/{p['category']}" if p.get("category") else "")
        + (f" × {p['limit']} 条" if p.get("limit") else " × 全量")
    )
    return (
        f"{icon} **{label}** `{run['run_id']}` · {scope} · 变体 `{p.get('variant', '')}`\n\n"
        f"`{bar}` {current}/{total}（{pct}）· 通过 {run['passed']} / 失败 {run['failed']}"
    )


def _render_report(data: dict) -> tuple[str, list[list]]:
    """报告 JSON →（摘要 Markdown，失败用例表格数据）。"""
    s = data.get("summary", {})
    traj = s.get("trajectory", {})

    lines = [
        "### 📋 报告摘要",
        f"- 数据集 `{data.get('dataset', '')}` · 变体 `{data.get('variant', '')}`"
        f" · 分类 `{data.get('category') or '全部'}` · 生成于 {data.get('generated_at', '')}"
        f" · commit `{data.get('git_sha') or 'unknown'}`",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 任务完成率 | **{s.get('task_completion_rate', 0):.1%}**（{s.get('passed', 0)}/{s.get('total', 0)}） |",
        f"| 工具调用准确率 | {s.get('tool_accuracy', 0):.1%} |",
        f"| 合规通过率 | {s.get('compliance_pass_rate', 0):.1%} |",
        f"| 平均耗时 | {s.get('avg_duration_s', 0)}s |",
    ]
    if "avg_vector_hits" in s:
        lines.append(
            f"| 检索命中 | 向量 {s.get('avg_vector_hits', 0)} 条/例 · 图谱 {s.get('avg_graph_hits', 0)} 条/例"
            f" · 图谱覆盖 {s.get('graph_coverage', 0):.1%} |"
        )
    lines.append("")

    by_cat = s.get("by_category") or {}
    if by_cat:
        lines += ["**分类明细**", "", "| 分类 | 通过率 |", "|------|--------|"]
        lines += [
            f"| {cat} | {stat['rate']:.1%}（{int(stat['passed'])}/{int(stat['total'])}） |"
            for cat, stat in by_cat.items()
        ]
        lines.append("")

    if traj:
        scored = lambda d: int(d.get("scored", 0))  # noqa: E731
        lines += [
            "**轨迹质量（D026 独立口径）**",
            "",
            f"- 顺序 {_rate(traj, 'order')}（{scored(traj.get('order', {}))} 条）· "
            f"路由 {_rate(traj, 'route')}（{scored(traj.get('route', {}))} 条）· "
            f"禁调 {_rate(traj, 'forbidden')}（{scored(traj.get('forbidden', {}))} 条）· "
            f"次数 {_rate(traj, 'limit')}（{scored(traj.get('limit', {}))} 条）· "
            f"入参 {_rate(traj, 'args')}（{scored(traj.get('args', {}))} 条）",
            f"- 冗余调用均值 {traj.get('redundancy', {}).get('avg_redundant_calls', 0)}"
            f"（有轨迹用例 {int(traj.get('redundancy', {}).get('cases_with_trace', 0))} 条）",
            "",
        ]

    fails = [
        [
            f.get("case_id", ""),
            str(f.get("category", "")),
            (f.get("answer", "") or "")[:60],
            ", ".join(f.get("used_tools", []) or []),
            (f.get("error", "") or "")[:60],
            f.get("duration_s", 0.0),
        ]
        for f in data.get("failures", [])
    ]
    return "\n".join(lines), fails


def _rate(traj: dict, key: str) -> str:
    return f"{traj.get(key, {}).get('rate', 1.0):.1%}"


def _build_trend_figure(points: list[dict], dataset: str, variant: str):
    """趋势点 → plotly 双指标折线（过滤条件为“全部”时不限）；无数据返回 None。"""
    rows = [
        p
        for p in points
        if (dataset in (None, "", "全部") or p["dataset"] == dataset)
        and (variant in (None, "", "全部") or p["variant"] == variant)
    ]
    if not rows:
        return None
    import plotly.graph_objects as go

    x = [p["time"] for p in rows]
    hover = [
        f"{p['label']}<br>{p['variant']} · {p['source']}<br>"
        f"commit {p['git_sha']}<br>{p['passed']}/{p['total']} 通过"
        for p in rows
    ]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=x,
            y=[p["task_completion_rate"] * 100 for p in rows],
            name="任务完成率",
            mode="lines+markers",
            hovertext=hover,
            hoverinfo="text",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=[p["tool_accuracy"] * 100 for p in rows],
            name="工具调用准确率",
            mode="lines+markers",
            hovertext=hover,
            hoverinfo="text",
        )
    )
    fig.update_layout(
        title="评测指标趋势（hover 查看运行/commit 明细）",
        xaxis_title="评测时间",
        yaxis_title="比率 (%)",
        yaxis_range=[0, 105],
        hovermode="x unified",
        legend={"orientation": "h"},
        margin={"l": 50, "r": 20, "t": 50, "b": 40},
    )
    return fig


async def load_meta() -> tuple:
    """页面加载：填充运行参数与趋势过滤的下拉可选项。"""
    try:
        m = await client.meta()
    except httpx.HTTPError:
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
    variant_names = [v["name"] for v in m["variants"]]
    return (
        gr.update(choices=m["datasets"]),
        gr.update(choices=["全部", *m["categories"]]),
        gr.update(choices=variant_names),
        gr.update(choices=["全部", *m["datasets"]]),
        gr.update(choices=["全部", *variant_names]),
    )


async def start_eval(
    dataset: str, category: str, limit: float | None, variant: str, state: dict
) -> tuple:
    """启动按钮：POST /runs，成功后进入轮询（按钮切换为运行中禁用态）。"""
    payload = {
        "dataset": dataset,
        "category": None if category in (None, "", "全部") else category,
        "limit": int(limit) if limit else None,
        "variant": variant,
    }
    try:
        result = await client.start_run(payload)
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text[:120]
        return f"⚠️ 启动失败：{exc.response.status_code} {detail}", state, gr.update()
    except httpx.HTTPError as exc:
        return f"⚠️ 无法连接后端（{API_BASE}）：{exc!r}", state, gr.update()
    state["run_id"] = result["run"]["run_id"]
    state.pop("done", None)
    return (
        f"🚀 已启动 `{state['run_id']}`（{payload['dataset']} × {payload['limit'] or '全量'}）…",
        state,
        gr.update(value="⏳ 评测运行中…", interactive=False),
    )


async def poll(state: dict, trend_dataset: str, trend_variant: str) -> tuple:
    """定时轮询：更新状态与日志；结束后刷新报告/趋势并自动加载最新报告。

    返回值与 tick 声明的输出组件一一对应（8 个：状态/日志/报告下拉/摘要/失败表/
    趋势图/state/启动按钮）——数量不符会使 Gradio 每次 tick 报错、界面冻结。
    """
    idle = (
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        state,
        gr.update(),
    )
    run_id = state.get("run_id") if isinstance(state, dict) else None
    if not run_id or state.get("done"):
        return idle
    try:
        run = await client.status(run_id)
    except httpx.HTTPError:
        return idle

    log_text = "\n".join(run.get("log_tail", [])[-LOG_LINES_SHOWN:])
    btn_running = gr.update(
        value=f"⏳ 评测运行中… {run['current']}/{run['total'] or '?'}", interactive=False
    )
    if run["status"] == "running":
        return (
            _status_md(run),
            log_text,
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            state,
            btn_running,
        )

    # 运行结束：置完成标记（state 随返回持久化，阻止后续 tick 重复加载），
    # 刷新报告列表并选中最新的本次报告，趋势图带新点重绘
    state["done"] = True
    try:
        reports = await client.reports()
        names = [r["name"] for r in reports]
        newest = run.get("report_name") or (names[0] if names else None)
        summary, fails = (
            _render_report(await client.report(newest)) if newest else ("（无报告产出）", [])
        )
    except httpx.HTTPError:
        names, newest, summary, fails = [], None, f"报告加载失败（运行状态：{run['status']}）", []
    try:
        fig = _build_trend_figure((await client.trends())["points"], trend_dataset, trend_variant)
    except httpx.HTTPError:
        fig = gr.update()
    head = (
        "✅ 评测完成"
        if run["status"] == "completed"
        else f"❌ 评测失败（exit={run.get('return_code')}）"
    )
    return (
        head + "\n\n" + _status_md(run),
        log_text,
        gr.update(choices=names, value=newest),
        summary,
        fails,
        fig,
        state,
        gr.update(value="▶️ 开始评测", interactive=True),
    )


async def refresh_reports() -> object:
    """手动刷新报告下拉（不改变已选值）。"""
    try:
        reports = await client.reports()
    except httpx.HTTPError:
        return gr.update()
    return gr.update(choices=[r["name"] for r in reports])


async def refresh_trends(dataset: str, variant: str):
    """拉取 /trends 并按过滤条件重绘趋势图；后端不可达时清空。"""
    try:
        data = await client.trends()
    except httpx.HTTPError:
        return None
    return _build_trend_figure(data["points"], dataset, variant)


async def show_report(name: str | None) -> tuple[str, list[list]]:
    """报告详情：摘要 Markdown + 失败用例表。"""
    if not name:
        return "选择左侧报告查看详情。", []
    try:
        data = await client.report(name)
    except httpx.HTTPError as exc:
        return f"⚠️ 报告加载失败：{exc!r}", []
    return _render_report(data)


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="claimflow 评测台") as demo:
        gr.Markdown(
            "# 📊 Agent 评测台\n"
            "选择参数后点击「开始评测」，进度实时刷新；评测报告落盘 `evals/reports/`，可在下方历史报告中回看。"
        )
        state = gr.State({})

        with gr.Row():
            dataset_dd = gr.Dropdown(label="数据集", value="main", choices=["main", "graph_assoc"])
            category_dd = gr.Dropdown(label="分类", value="全部", choices=["全部"])
            variant_dd = gr.Dropdown(label="变体", value="baseline", choices=["baseline"])
            limit_num = gr.Number(label="条数上限（空 = 全量）", value=10, precision=0, minimum=1)
            start_btn = gr.Button("▶️ 开始评测", variant="primary", scale=0)

        status_md = gr.Markdown("待启动。")
        log_box = gr.Textbox(label="运行日志（逐用例 PASS/FAIL）", lines=12, interactive=False)

        gr.Markdown("## 📈 趋势")
        with gr.Row():
            trend_dataset_dd = gr.Dropdown(label="数据集过滤", value="全部", choices=["全部"])
            trend_variant_dd = gr.Dropdown(label="变体过滤", value="全部", choices=["全部"])
            trend_refresh_btn = gr.Button("🔄 刷新趋势", scale=0)
        trend_plot = gr.Plot(label="任务完成率 / 工具调用准确率 随时间变化")

        gr.Markdown("## 🗂️ 历史报告")
        with gr.Row():
            reports_dd = gr.Dropdown(label="报告文件", choices=[], scale=5)
            refresh_btn = gr.Button("🔄 刷新", scale=0)
        summary_md = gr.Markdown("选择报告查看详情。")
        gr.Markdown("### 失败用例明细")
        fails_df = gr.Dataframe(
            headers=["用例", "分类", "回答(截断)", "实际工具", "错误", "耗时(s)"],
            datatype=["str", "str", "str", "str", "str", "number"],
            interactive=False,
        )

        demo.load(
            load_meta,
            outputs=[dataset_dd, category_dd, variant_dd, trend_dataset_dd, trend_variant_dd],
        )
        demo.load(refresh_reports, outputs=[reports_dd])
        demo.load(refresh_trends, inputs=[trend_dataset_dd, trend_variant_dd], outputs=[trend_plot])

        start_btn.click(
            start_eval,
            [dataset_dd, category_dd, limit_num, variant_dd, state],
            [status_md, state, start_btn],
        )
        timer = gr.Timer(POLL_SECONDS)
        timer.tick(
            poll,
            inputs=[state, trend_dataset_dd, trend_variant_dd],
            outputs=[
                status_md,
                log_box,
                reports_dd,
                summary_md,
                fails_df,
                trend_plot,
                state,
                start_btn,
            ],
        )
        reports_dd.input(show_report, [reports_dd], [summary_md, fails_df])
        refresh_btn.click(refresh_reports, outputs=[reports_dd])
        trend_refresh_btn.click(refresh_trends, [trend_dataset_dd, trend_variant_dd], [trend_plot])
    return demo


demo = build_ui()

if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=int(os.getenv("GRADIO_EVAL_PORT", "7861")))
