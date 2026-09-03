# skills/ —— 作业规程包（D039）

每个目录 = 一个调度/工作阶段；每个文件 = 一个险种（或跨险种共用）的作业规程。

装载规则（`services/skills.py`）：
- `skills/<stage>/<line>.md`（如 `skills/liability_judge/medical.md`）优先
- 缺失时回退 `skills/<stage>/_shared.md`
- 两者皆缺 → 仅用代码内置 base prompt 并记 warning

编写约定：
- 内容 = 该岗位的 SOP 步骤 + 输出要求 + 红线清单 +（可选）few-shot 示例
- 面向模型阅读：指令式短句，避免 marketing 文风
- 改动任何 skill 必须跑金样本回归（`scripts/verify_orchestrator.py` / evals），
  可接 A/B 框架做新旧规程对比（variant：skill_old / skill_new）
