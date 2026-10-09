# otelcol/ —— OpenTelemetry Collector 配置

OTLP gRPC 接收（`:4317`）→ OTLP HTTP 转发 Jaeger（`:4318`），见 `config.yaml`。

## 启动

```bash
docker compose --profile tracing up -d   # otel-collector + jaeger
# .env 需OTEL_ENABLED=true（本地 .env 已默认开启）
docker compose up -d --force-recreate app   # 改过 .env 必须重建容器，restart 不够
```

Jaeger UI：`:16686`。

## span结构

trace_id贯穿 API 入口 → 节点 → LLM：

| span | 来源 | 关键属性 |
|---|---|---|
| FastAPI server span |中间件 instrumentation | path / method / status |
| `case.deliver` / `case.<stage>` | `traced_span`（含 case_id） | `claimflow.phase` |
| `llm.<model>` | `observed_ainvoke` 统一包装（T039） | `gen_ai.request.model`、`gen_ai.usage.input/output_tokens`、**`gen_ai.usage.cache_read_tokens` / `cache_miss_tokens`** |
| 工具 / 合规 span | `traced_span` | `claimflow.tool.name`、`claimflow.compliance.verdict/risk_score` |

**缓存原值只有这里能看到**（T165）：Prometheus 的 Counter 已聚合掉单次调用的
hit/miss，按 trace_id 查 Jaeger 才能定位"这案子哪次调用没命中"。

## 采样率（T166）

`OTEL_SAMPLING_RATIO` 默认 **0.2**（此前 1.0 全采样）。全采样下每请求数十 span，
磁盘与 CPU 均需付账；排障只需 trace_id 级定位单次调用，不需要全量。

compose 已透传该变量（此前只透传 `OTEL_ENABLED`/`OTEL_ENDPOINT`，容器内落config 默认值）。
容器内 endpoint 固定 `http://otel-collector:4317`（`.env` 的 `localhost:4317`
是宿主机直跑进程用的）。

## 关闭

`OTEL_ENABLED=false` → `setup_tracing` 直接跳过，OTel API 层为 noop tracer，
零开销零侵入。
