# otelcol/ —— OpenTelemetry Collector 配置

OTLP 接收 → Jaeger 导出（`otelcol.yaml`）。

启动：`docker compose --profile tracing up -d` + `.env` 设 `OTEL_ENABLED=true`
→ Jaeger UI `:16686`。

链路覆盖：交付队列 → 核赔阶段（`_timed` span，含 case_id）→ orchestrator 路由 →
LLM 调用。OTel 关闭时 noop tracer 零开销（T128）。
