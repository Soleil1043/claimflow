# skills/ —— 作业规程包（D039）

每个目录 = 一个调度/工作阶段；每个文件 = 一个险种（或跨险种共用）的作业规程。
**准确率迭代改文本不改代码**——这是本项目的人工调优面。

## 现状

四险种已上线（T120），共 21 份规程：

| 阶段 | medical | auto | property | accident |
|------|---------|------|----------|----------|
| material_review | ✅ | ✅ | ✅ | ✅ |
| policy_verify | ✅ | ✅ | ✅ | ✅ |
| fraud_check | _shared | ✅ | ✅ | ✅ |
| liability_judge | ✅ | ✅ | ✅ | ✅ |
| decision_writer | ✅ | ✅ | ✅ | ✅ |

`orchestrator/_shared.md` 为调度规程（全险种共用）。

## 装载规则（`services/skills.py`）

- `skills/<stage>/<line>.md`（如 `skills/liability_judge/medical.md`）优先
- 缺失时回退 `skills/<stage>/_shared.md`
- 两者皆缺 → 仅用代码内置 base prompt 并记 warning

## 编写约定

- 内容 = 该岗位的 SOP 步骤 + 输出要求 + 红线清单 +（可选）few-shot 示例
- 面向模型阅读：指令式短句，避免 marketing 文风
- **文本不得先于能力**——规程不得承诺架构没有的字段/行为（T127 车险教训）
- 改动任何 skill 必须跑金样本回归（`evals/adjudication_suite` 或
  `scripts/verify_orchestrator.py`），六门不退化才算过
