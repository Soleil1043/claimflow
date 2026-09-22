# prometheus/ —— 指标抓取配置

`prometheus.yml`：抓取 app 容器 `:8000/metrics`（15s 间隔）。

启动：`docker compose --profile monitoring up -d`。
后端裸指标不加栈也可看：`http://localhost:8000/metrics`。
仪表盘见 [`../grafana/`](../grafana/)。
