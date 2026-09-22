# grafana/ —— 核赔监控仪表盘

`claimflow-adjudication.json`：核赔业务 8 面板——自动结案率 / 转人工率 / 调度调用 /
调度健康 / 阶段耗时 P95 / 端到端耗时 / 案件量 / 核定金额。

数据源指向 Prometheus（datasource 自动配置随 compose 注入）。

启动：`docker compose --profile monitoring up -d` → Grafana `:3000`。
指标口径见 [`../services/observability/metrics.py`](../services/observability/metrics.py)。
