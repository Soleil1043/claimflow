# docs/ —— 设计文档与导出物

| 内容 | 文件 |
|------|------|
| **架构总览**（现状系统长什么样） | [`architecture.md`](architecture.md) |
| 架构设计工程版（为什么这样设计） | [`claimflow-新架构设计.md`](claimflow-新架构设计.md) |
| 同上人话版导读 | [`claimflow-新架构设计-人话版.md`](claimflow-新架构设计-人话版.md) |
| 主图/流程图（Mermaid 导出 HTML + 校验截图） | `diagrams/` |
| 界面截图（工作台 / Jaeger / 三界面重构） | `screenshots/` `diagrams/t0*.png` |
| 练习手册（图编排→毕业项目八课） | [`exercises/`](exercises/README.md) |

## 约定

- `architecture.md` 随架构演进更新（上次：**T167/T168 全量刷新，2026-10-09**——
  补 Prompt 缓存观测三来源提取 + stage 维度、对抗集拆分、单Agent 消融、RAG 评测、
  T153 队列三分、T171-176 决策索引）；
- 设计文档（新架构设计）是历史决策快照，不回改——新决策进 `.agent/decisions.md`；
- 图表导出物（diagrams/*.visual-check.*）为 Mermaid Live 校验产物，可重建。

## 外部参考

| 文档 | 内容 |
|---|---|
| [`../README.md`](../README.md) | 现状总览：成果指标 / 能力清单 / 启动栈 / 评测基线 |
| [`../.agent/decisions.md`](../.agent/decisions.md) | 决策链 D001-D076（每条含选项 / 理由 / 最终选择） |
| [`../.agent/progress.md`](../.agent/progress.md) | 构建日志（全程追加，含踩坑记录） |
| [`../evals/reports/t163_cache_hit_report.md`](../evals/reports/t163_cache_hit_report.md) | Prompt 缓存命中率实测报告（含 before/after 对比与归因纠偏） |
| [`../evals/reports/t160_ablation_report.md`](../evals/reports/t160_ablation_report.md) | 单Agent 消融实验报告（多Agent 88.9% vs 单 Agent 60.8%） |
| [`../evals/reports/t159_redteam_evidence.md`](../evals/reports/t159_redteam_evidence.md) | 8 条注入样本原文 + 分类法映射 |
