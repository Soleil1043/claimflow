# workflows/ —— 图编排

## case_graph.py

核赔主图定义与编译——整个系统唯一的工作流入口：

- **节点接线**：intake → orchestrator ⇄ 六 worker → compliance_gate →
  auto_adjudicate / human_gate；
- **静态合规门**：`decision_generate → compliance_gate` 焊死的边，
  有图结构测试断言，任何路由决策无法绕过；
- **Send 并行**：orchestrator 的 `route_dispatch` 条件边返回 `[Send(state)]`
  实现一轮多 worker 并行；
- **回边派生**：worker → orchestrator 回边由 StageSpec 注册表派生，不手写；
- **checkpoint**：prod = AsyncPostgresSaver（thread_id = case_id），
  dev = InMemorySaver；`_timed` 包装每阶段出 OTel span。

图结构测试（静态边断言 / 回边断言）在 `tests/workflows/`——改图先看那里的断言。
架构见 [`../docs/architecture.md`](../docs/architecture.md) §3。
