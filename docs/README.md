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

- `architecture.md` 随架构演进更新（上次：T145 后全量刷新，2026-09-22）；
- 设计文档（新架构设计）是历史决策快照，不回改——新决策进 `.agent/decisions.md`；
- 图表导出物（diagrams/*.visual-check.*）为 Mermaid Live 校验产物，可重建。
