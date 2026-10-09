# prometheus/ —— 指标抓取配置

`prometheus.yml`：抓取 app 容器 `:8000/metrics`（`scrape_interval` 15s）。
容器间用 compose 服务名互访（`app:8000`，非 localhost）。

## 保留期（T166）

**TSDB 保留期不在配置文件里**——Prometheus v3 已把它移出配置，只能走命令行 flag：

```yaml
# docker-compose.yml 里 prometheus 服务的 command
command:
  - "--config.file=/etc/prometheus/prometheus.yml"
  - "--storage.tsdb.retention.time=30d"
```

配置文件里三种写法全部报 `field not found in type config.plain`（promtool 实测三轮）：
`global.retention_time` ✗ / `storage.tsdb.retention.time` ✗ / `storage.tsdb.retention_time` ✗。
`--storage.tsdb.max-samples-per-query` 在 v3.1 也不存在（unknown long flag）。

生效确认：`curl localhost:9090/api/v1/status/flags | jq '.data["storage.tsdb.retention.time"]'` → `30d`。

数据落 Docker 卷 `claimflow_prometheus_data`（`docker compose down` 保卷，`down -v` 才删）。

## 启动

```bash
docker compose --profile monitoring up -d   # → Prometheus :9090
```

后端裸指标不加栈也可看：`http://localhost:8000/metrics`。
仪表盘见 [`../grafana/`](../grafana/)，指标定义见
[`../services/observability/metrics.py`](../services/observability/metrics.py)。

## 指标口径注意（T162/T164）

`claimflow_llm_cache_tokens_total` 的 label 是 `model × stage × result`：

- `stage` 由 `token_tracker.current_phase()` 上下文自动携带（`phase_ainvoke` 标注），
  或 create_agent 子图经 `LlmUsageCallbackHandler` 传入；
- Counter 在**进程内存**里，app 重启归零——Prometheus 侧用 `increase()`/`rate()`
  而非裸值查询，负跳变由函数自动处理；
- 模型不返回缓存字段时**不记该维度**（不臆造 0）。
