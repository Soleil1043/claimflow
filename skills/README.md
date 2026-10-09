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

装配顺序是「先 format base prompt → 再拼 skill」，这样 skill 文本可含任意大括号
（few-shot JSON 等）不受 format 影响。**但 base prompt 内不得填动态数据**（见下）。

## 编写约定

- 内容 = 该岗位的 SOP 步骤 + 输出要求 + 红线清单 +（可选）few-shot 示例
- 面向模型阅读：指令式短句，避免 marketing 文风
- **文本不得先于能力**——规程不得承诺架构没有的字段/行为（T127 车险教训）
- **规程进 system，案件数据进 user message**（T165）：本目录的文本全部装配进
  `SystemMessage`，所以它必须是**逐字稳定**的——同一 stage×line 内每次调用内容
  完全一致，才能整段命中 DeepSeek 前缀缓存。案件快照 / 材料提取结果 / 理算事实
  等每案唯一的数据不进这里，由调用方放 `HumanMessage` 并用 `<<<DATA…>>>DATA` 定界。
  **改本目录的文本不影响缓存命中**（反而有助于稳定），但**在base prompt 里插
  动态占位符会**——那会让前缀分叉点落在唯一数据开头，命中率归零（实测
  orchestrator 改造前 0%）。
- 改动任何 skill 必须跑金样本回归（`evals/adjudication_suite` 或
  `scripts/verify_orchestrator.py`），六门不退化才算过；涉及注入面的改动还要跑
  `--dataset adversarial`（8 案硬门）与 `adversarial_holdout`（30 案盲测）。
