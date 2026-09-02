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

from ui.theme import APP_CSS, build_theme

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

    async def health(self) -> bool:
        """后端健康探测（头部状态点）。"""
        try:
            resp = await self._http.get("/health", timeout=5)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False


client = EvalClient(API_BASE)


def _header_html(backend_ok: bool | None) -> str:
    """浮层 chrome 头部：品牌块 + 状态 pill（T061 视觉深化）。"""
    if backend_ok is None:
        pill, dot, label = "muted", "warn", "检测中…"
    elif backend_ok:
        pill, dot, label = "ok", "ok", "后端已连接"
    else:
        pill, dot, label = "err", "err", "后端不可达"
    return f"""
<div class="cf-header">
  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;">
    <div style="display:flex;align-items:center;gap:12px;">
      <div class="cf-logo">CF</div>
      <div>
        <div class="cf-title">Agent 评测台</div>
        <div class="cf-subtitle">一键评测 · 实时进度 · 趋势对比 · 报告回看</div>
      </div>
    </div>
    <span class="cf-pill {pill}" style="white-space:nowrap;">
      <span class="cf-status-dot {dot}"></span>{label}
    </span>
  </div>
</div>"""


def _section_html(kicker: str, title: str, title_cls: str = "cf-h2") -> str:
    """区块标题体系：kicker 大写小字 + 蓝色短划线 + 标题（D031）。"""
    return (
        f'<div class="cf-section"><div class="cf-kicker">{kicker}</div>'
        f'<div class="{title_cls}">{title}</div></div>'
    )


async def _check_backend() -> dict:
    """页面加载：探测后端健康，更新头部状态点。"""
    ok = await client.health()
    return gr.update(value=_header_html(ok))


def _status_html(run: dict, head: str = "") -> str:
    """运行状态 → 卡片化 HTML（状态 pill + 渐变进度条 + 计数，T056/T061）。

    注：本区由 gr.Timer 每 2s 高频更新，禁用入场动画（否则随 poll 重放闪烁）。
    """
    current, total = run["current"], run["total"]
    ratio = current / total if total else 0
    pct = f"{ratio:.0%}" if total else "—"
    pill_cls, label = {
        "running": ("info", "运行中"),
        "completed": ("ok", "已完成"),
        "failed": ("err", "失败"),
    }.get(run["status"], ("muted", run["status"]))
    p = run.get("params", {})
    scope = (
        p.get("dataset", "")
        + (f"/{p['category']}" if p.get("category") else "")
        + (f" × {p['limit']} 条" if p.get("limit") else " × 全量")
    )
    head_html = f'<div class="cf-kicker" style="margin-bottom:10px;">{head}</div>' if head else ""
    return f"""
<div class="cf-card">
  {head_html}
  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
    <span class="cf-pill {pill_cls}">{label}</span>
    <code style="font-size:12px;">{run["run_id"]}</code>
    <span style="font-size:13px;color:var(--cf-text-2);">{scope} · 变体 {p.get("variant", "")}</span>
  </div>
  <div class="cf-progress" style="margin:10px 0 6px;">
    <div style="width:{ratio:.0%};"></div>
  </div>
  <div style="font-size:13px;color:var(--cf-text-2);font-variant-numeric:tabular-nums;">
    {current}/{total}（{pct}）· 通过 {run["passed"]} / 失败 {run["failed"]}
  </div>
</div>"""


def _bar(rate: float) -> str:
    """mini 进度条：通过率语义配色（≥80% 绿 / ≥60% 橙 / 其余红，D031）。"""
    tone = "ok" if rate >= 0.8 else ("warn" if rate >= 0.6 else "err")
    return f'<span class="cf-bar {tone}"><i style="width:{rate:.0%};"></i></span>'


def _kpi(label: str, value: str, foot: str = "") -> str:
    """KPI 大数字卡（层级 = 字重+字号+tracking，tabular-nums 对齐）。"""
    return f"""
<div class="cf-kpi">
  <div class="k-label">{label}</div>
  <div class="k-value">{value}</div>
  <div class="k-foot">{foot}</div>
</div>"""


def _render_report(data: dict) -> tuple[str, list[list]]:
    """报告 JSON →（摘要 HTML：KPI 卡 + 分类表 + 轨迹 pill，失败用例表格数据）。"""
    s = data.get("summary", {})
    traj = s.get("trajectory", {})

    parts = [
        '<div style="font-size:16px;font-weight:600;letter-spacing:-0.01em;">📋 报告摘要</div>',
        '<div style="margin-top:4px;font-size:12.5px;color:var(--cf-text-2);">'
        f"数据集 <code>{data.get('dataset', '')}</code> · 变体 <code>{data.get('variant', '')}</code>"
        f" · 分类 <code>{data.get('category') or '全部'}</code>"
        f" · 生成于 {data.get('generated_at', '')}"
        f" · commit <code>{data.get('git_sha') or 'unknown'}</code></div>",
        '<div class="cf-kpi-grid">',
        _kpi(
            "任务完成率",
            f"{s.get('task_completion_rate', 0):.1%}",
            (
                f"CI [{s['wilson_ci'][0]:.1%}, {s['wilson_ci'][1]:.1%}]"
                if isinstance(s.get("wilson_ci"), list)
                and len(s["wilson_ci"]) == 2
                else f"{s.get('passed', 0)}/{s.get('total', 0)} 通过"
            ),
        ),
    ]
    # 工具准确率：空分母（tool_scored_total=0）显示 N/A 而非误导性的 100%（T073）
    if s.get("tool_scored_total"):
        parts.append(_kpi("工具调用准确率", f"{s.get('tool_accuracy', 0):.1%}"))
    else:
        parts.append(_kpi("工具调用准确率", "N/A", "本轮无工具考核用例"))
    parts.append(_kpi("合规通过率", f"{s.get('compliance_pass_rate', 0):.1%}"))
    # 北极星指标（T064/T065）：有转人工标注/行为才展示
    if s.get("human_scored") or s.get("human_intervened"):
        parts.append(
            _kpi(
                "转人工召回",
                f"{s.get('human_recall', 0):.1%}",
                f"{int(s.get('human_scored', 0))} 条期望 / "
                f"{int(s.get('human_intervened', 0))} 条实际",
            )
        )
    if s.get("intent_scored"):
        parts.append(
            _kpi(
                "意图准确率",
                f"{s.get('intent_accuracy', 0):.1%}",
                f"{int(s.get('intent_scored', 0))} 条标注",
            )
        )
    if s.get("judge_scored"):
        parts.append(
            _kpi(
                "LLM-judge 判过率",
                f"{s.get('judge_pass_rate', 0):.1%}",
                f"{int(s.get('judge_scored', 0))} 条 · 独立口径未计入完成率",
            )
        )
    dur_foot = f"p95 {s.get('p95_duration_s', 0)}s"
    if s.get("tokens_per_case") is not None:
        dur_foot += f" · {s.get('tokens_per_case')} tok/例"
    parts.append(_kpi("平均耗时", f"{s.get('avg_duration_s', 0)}s", dur_foot))
    if "avg_vector_hits" in s:
        parts.append(
            _kpi(
                "检索命中",
                f"{s.get('avg_vector_hits', 0)}",
                f"向量条/例 · 图谱 {s.get('avg_graph_hits', 0)} 条/例"
                f" · 图谱覆盖 {s.get('graph_coverage', 0):.1%}",
            )
        )
    parts.append("</div>")

    by_cat = s.get("by_category") or {}
    if by_cat:
        rows = "".join(
            "<tr style='border-top:1px solid var(--cf-hairline);'>"
            f"<td style='padding:6px 10px;'>{cat}</td>"
            f"<td style='padding:6px 10px;font-variant-numeric:tabular-nums;'>"
            f"{stat['rate']:.1%}（{int(stat['passed'])}/{int(stat['total'])}）{_bar(stat['rate'])}</td></tr>"
            for cat, stat in by_cat.items()
        )
        parts += [
            '<div style="margin-top:14px;font-size:13px;font-weight:600;">分类明细</div>',
            "<table style='width:100%;margin-top:6px;font-size:13px;border-collapse:collapse;'>"
            "<thead><tr style='color:var(--cf-text-2);font-size:12px;text-align:left;'>"
            "<th style='padding:4px 10px;font-weight:500;'>分类</th>"
            "<th style='padding:4px 10px;font-weight:500;'>通过率</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>",
        ]

    if traj:
        scored = lambda d: int(d.get("scored", 0))  # noqa: E731
        dims = [
            ("顺序", "order"),
            ("路由", "route"),
            ("禁调", "forbidden"),
            ("次数", "limit"),
            ("入参", "args"),
        ]
        pills = "".join(
            f'<span class="cf-pill info">{name} {_rate(traj, key)}'
            f"（{scored(traj.get(key, {}))} 条）</span>"
            for name, key in dims
        )
        parts += [
            '<div style="margin-top:14px;font-size:13px;font-weight:600;">轨迹质量（D026 独立口径）</div>',
            f'<div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap;">{pills}</div>',
            '<div style="margin-top:6px;font-size:12.5px;color:var(--cf-text-2);">'
            f"冗余调用均值 {traj.get('redundancy', {}).get('avg_redundant_calls', 0)}"
            f"（有轨迹用例 {int(traj.get('redundancy', {}).get('cases_with_trace', 0))} 条）</div>",
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
    return "\n".join(parts), fails


def _rate(traj: dict, key: str) -> str:
    """维度率展示：scored=0 显示 N/A（未标注不考核，消除"无数据=满分"误读，T073）。"""
    d = traj.get(key, {})
    if not d.get("scored"):
        return "N/A"
    return f"{d.get('rate', 1.0):.1%}"


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
        + (
            f"<br>CI [{p['task_completion_ci'][0]:.1%}, {p['task_completion_ci'][1]:.1%}]"
            if p.get("task_completion_ci")
            else ""
        )
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
    dataset: str, category: str, limit: float | None, variant: str, judge: bool, state: dict
) -> tuple:
    """启动按钮：POST /runs，成功后进入轮询（按钮切换为运行中禁用态）。"""
    payload = {
        "dataset": dataset,
        "category": None if category in (None, "", "全部") else category,
        "limit": int(limit) if limit else None,
        "variant": variant,
        "judge": bool(judge),
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
            _status_html(run),
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
        _status_html(run, head=head),
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
    """组装界面（T056 Apple 化 + T061 视觉深化：Tabs 分区/区块标题/状态卡）。

    注：Gradio 6 起 theme/css 从 Blocks 构造器移至 launch()。
    """
    with gr.Blocks(title="claimflow 评测台") as demo:
        header = gr.HTML(_header_html(None))
        state = gr.State({})

        gr.HTML(_section_html("RUN", "启动评测"))
        with gr.Group(elem_classes=["cf-card", "cf-rise"]):
            with gr.Row():
                dataset_dd = gr.Dropdown(label="数据集", value="main", choices=["main", "graph_assoc"])
                category_dd = gr.Dropdown(label="分类", value="全部", choices=["全部"])
                variant_dd = gr.Dropdown(label="变体", value="baseline", choices=["baseline"])
                limit_num = gr.Number(label="条数上限（空 = 全量）", value=10, precision=0, minimum=1)
                judge_cb = gr.Checkbox(
                    label="LLM-judge 二层判分（独立口径）",
                    value=False,
                    scale=0,
                )
                start_btn = gr.Button("▶️ 开始评测", variant="primary", scale=0)

        status_md = gr.HTML(
            '<div class="cf-card"><span style="font-size:13px;color:var(--cf-text-2);">'
            "待启动。选择参数后点击「开始评测」。</span></div>"
        )
        log_box = gr.Textbox(
            label="运行日志（逐用例 PASS/FAIL）",
            lines=12,
            interactive=False,
            elem_classes=["cf-log"],
        )

        with gr.Tabs():
            with gr.Tab("📈 趋势"):
                with gr.Row():
                    trend_dataset_dd = gr.Dropdown(label="数据集过滤", value="全部", choices=["全部"])
                    trend_variant_dd = gr.Dropdown(label="变体过滤", value="全部", choices=["全部"])
                    trend_refresh_btn = gr.Button("🔄 刷新趋势", scale=0)
                trend_plot = gr.Plot(label="任务完成率 / 工具调用准确率 随时间变化")
            with gr.Tab("🗂️ 历史报告"):
                with gr.Row():
                    reports_dd = gr.Dropdown(label="报告文件", choices=[], scale=5)
                    refresh_btn = gr.Button("🔄 刷新", scale=0)
                summary_md = gr.HTML(
                    '<div class="cf-card"><span style="font-size:13px;color:var(--cf-text-2);">'
                    "选择报告查看详情。</span></div>"
                )
                gr.HTML(_section_html("FAILURES", "失败用例明细", "cf-h3"))
                fails_df = gr.Dataframe(
                    headers=["用例", "分类", "回答(截断)", "实际工具", "错误", "耗时(s)"],
                    datatype=["str", "str", "str", "str", "str", "number"],
                    interactive=False,
                )

        demo.load(_check_backend, outputs=[header])
        demo.load(
            load_meta,
            outputs=[dataset_dd, category_dd, variant_dd, trend_dataset_dd, trend_variant_dd],
        )
        demo.load(refresh_reports, outputs=[reports_dd])
        demo.load(refresh_trends, inputs=[trend_dataset_dd, trend_variant_dd], outputs=[trend_plot])

        start_btn.click(
            start_eval,
            [dataset_dd, category_dd, limit_num, variant_dd, judge_cb, state],
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
    demo.launch(
        server_name="127.0.0.1",
        server_port=int(os.getenv("GRADIO_EVAL_PORT", "7861")),
        theme=build_theme(),
        css=APP_CSS,
    )
