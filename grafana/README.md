# grafana/ —— 核赔监控仪表盘

两个仪表盘共**22 面板**，数据源均指向 Prometheus（datasource 随 compose 注入）。

| 仪表盘 | uid | 面板数 | 内容 |
|---|---|---|---|
| `claimflow-adjudication.json` | `claimflow-adjudication` | 10 | 核赔业务：自动结案率 / 转人工率 / 调度调用·案件 / 调度健康（守卫纠错+兜底）/ 阶段耗时 P95 / 案件端到端耗时 / 案件量趋势（按终态）/ 核定金额分布 / 单案 Token 均值 / 单案 LLM 成本估算 ¥（T177） |
| `claimflow-overview.json` | `claimflow-overview` | 12 | 服务总览（工具成功率 / 案件转人工率 / P95 时延 / 工具与 LLM 调用量 / 熔断拒绝 / 工具缓存命中率 T177）+ **Prompt 缓存观测**（下表）。v1 陈旧面板（人工转接率 / 合规三态 / 单轮 P95 / 对话轮次——引用已删指标）已于 T179 清理，转人工率改由 CASES_TOTAL 终态推导替换 |

## Prompt 缓存面板（T162-T166）

| 面板 | 类型 | promql 要点 |
|---|---|---|
| Prompt 缓存命中率（全局 / 分调用点） | stat | `increase[$__range]` 派生，阈值 red<30% / yellow<60% / green≥60% |
| 缓存 Token 量（按调用点/模型/结果） | 时序 | `sum by (stage, model, result)`，hit（廉价）/ miss（全价）堆叠 |
| 单次调用命中 token 均值 | 时序 | hit 速率 / `llm_calls` 速率，`clamp_min` 防除零 |
| 缓存命中率趋势（按调用点） | 时序 | 按 `stage` 分列的命中率 |

**stage 维度是这套面板的关键**（T164）：能区分「布局缺陷」（orchestrator /
material_review / decision_writer）与「天然 miss」（ocr 材料提取——prompt 主体
即唯一文档内容，前缀天然短）两类场景。没有这个维度，布局改造收益无法评估，甚至
会把材料提取的 miss 误判成布局问题。

单次调用的 hit/miss **原值**不在这里（Counter 已聚合），查 Jaeger `:16686`。

## 告警规则

`provisioning/alerting/rules.yml` 三条（T154）：
① 自动结案率骤降（1h 有量 >5 且 auto_issued 占比 <30% 持续 10m）
② 交付队列积压（queued >20 持续 5m）
③ LLM 失败率（15m 有量 >10 且错误占比 >50% 持续 5m）

演示栈无通知接收端，规则在 UI 呈现，部署方自行接通道；无独立 Alertmanager。

## 启动

```bash
docker compose --profile monitoring up -d   # → Grafana :3000（免登录直读）
```

指标口径见 [`../services/observability/metrics.py`](../services/observability/metrics.py)，
指标健康速查：`http://localhost:8000/metrics`（无需监控栈）。

实测注意：引用不存在的指标名时 Grafana **静默显示 No data**（不报错）——
写面板前先核对 `metrics.py` 的指标名、类型（Counter/Histogram）与 labelnames。
