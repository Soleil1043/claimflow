# claimflow 评测体系审计报告（工程版）

> 生成：2026-09-03。基于当前工作区状态（62/62 任务全部完成后）。
> 用途：本文件是**供 AI 编程工具消费的整改规格**——第 4 章可直接拆为任务追加进 `.agent/tasks.md` 执行。人话版解读见 `docs/eval-audit-for-human.md`。

---

## 1. 审计范围与方法

| 检查对象 | 文件 |
|---|---|
| 判分逻辑 | `evals/metrics.py`、`evals/trajectory.py`、`evals/schemas.py` |
| 运行器与实验 | `evals/test_suite.py`、`evals/ab_test.py`、`evals/variants.py` |
| 数据集 | `evals/datasets/eval_dataset.json`（200 条）、`evals/datasets/eval_graph_assoc.json`（24 条） |
| 历史报告 | `evals/reports/baseline.json`、`t048_phase5_regression.json` 等 20 份 |
| 平台层 | `services/eval_history.py`、`services/eval_runner.py`、`app/api/v1/evals.py`、`ui/eval_app.py` |
| 测试 | `tests/evals/`（5 个测试文件） |
| CI | `.github/workflows/ci.yml` |

所有数字均可复现，示例：

```bash
# 数据集标注分布
python -c "
import json
from collections import Counter
d=json.load(open('evals/datasets/eval_dataset.json',encoding='utf-8'))
c=Counter()
for x in d['cases']:
    for k in ['expected_tool_order','expected_tool_args','expected_route','forbidden_tools','max_tool_calls']:
        if x.get(k): c[k]+=1
    if x.get('expect_human_intervention'): c['expect_human']+=1
print(dict(c))"
# 预期输出：{'expected_tool_order': 6, 'expected_tool_args': 7, 'expected_route': 6, 'forbidden_tools': 5, 'max_tool_calls': 6}   ← expect_human 恒缺失
```

## 2. 现状盘点（资产清单）

**基线指标（全量 200 条，真实 LLM）**

| 报告 | 完成率 | 工具准确率 | 合规通过率 | 平均耗时 | 轨迹块 |
|---|---|---|---|---|---|
| baseline.json（2026-08-25） | 88.0% | 95.3% | 99.5% | 18.99s | null |
| t048_phase5_regression.json（2026-08-27） | 87.0% | 94.7%（187 例计入分母） | 95.5% | 12.71s | null |

分类明细（两次接近）：FAQ 93.3% / 单领域 75–78% / 多步 92.5% / 边界 90%。

**数据集标注覆盖**

| 标注维度 | 覆盖数 / 200 | 说明 |
|---|---|---|
| expected_tools | 131（FAQ 30/30、单域 52/60、多步 49/80、边界 0/30） | 边界类全部靠答案要点判分 |
| any_of | 188（平均 2.64 词/例） | 承担 94% 用例的实际判分 |
| must_include | 14 | 严格断言极稀疏 |
| must_not_include | 4 | 违规话术防线仅 4 例 |
| expected_intent | 80 | **判分代码零消费（死标注，见 BUG-002）** |
| expect_human_intervention=True | **0** | 北极星指标零覆盖（见 BUG-001） |
| 轨迹五维（order/route/forbidden/args/limit） | 6/6/5/7/6 | 机制完备但标注率约 3% |

**平台能力（达标项）**：A/B 变体注册表 + checkpoint 变体隔离 + 双比例 z 检验 + Prometheus token 差分；eval_runs 落库（git_sha、fail-open）；REST API（runs/reports/meta/trends）；Gradio 评测台（进度轮询/趋势折线/失败明细）；并发 409 守卫；判分纯函数有 5 个测试文件覆盖。

## 3. 缺陷清单

### BUG-001　human_precision 指标退化（P0）

- **位置**：`evals/metrics.py` L144-147
- **现状**：`human_precision = len(human_expected) / len(human_expected)`——分子分母同源，非空即恒为 1.0，空为 0.0。两份全量报告均为 0.0（说明全量运行中无任何用例触发转人工），该指标从未产出过有效信息。
- **叠加缺陷**：主数据集 0 条 `expect_human_intervention=True` 用例，即使修好公式也无分母。
- **影响**：项目北极星目标"人工转接率 62%→37%"（spec.md §1）在评测体系中**不可测、不可证**。
- **连带**：合规 REJECT 路径（F10）→ 转人工标记的端到端行为从未被评测覆盖。

### BUG-002　expected_intent 死标注（P0）

- **位置**：`evals/schemas.py`（定义）、`evals/metrics.py::score_case` / `aggregate`（无消费）
- **现状**：80 条（全部 multi_step）标注了 `expected_intent`，但判分与聚合代码均不读取。`result.intent` 在 run_case 输入中初始化为 None 后未回填比对。
- **影响**：F03 验收标准"意图分类准确率 ≥90%"无法在评测报告中呈现。

### GAP-001　答案判分仅关键词子串（P1）

- **证据**：`metrics.py::score_case` 的判分原语只有 `_norm` 归一化子串匹配；any_of 平均 2.6 词/例。
- **风险**：①金额幻觉不可检（4640 算成 5640，关键词不涉及数字即 PASS）；②否定语境误杀（"不能保证赔付"命中 must_not_include 的"保证赔付"）。
- **企业级要求**：数值精确断言 + LLM-as-judge（faithfulness/helpfulness rubric）+ judge 校准流程。

### GAP-002　RAG 质量指标缺失（P1）

- **现状**：`avg_vector_hits` / `avg_graph_hits` / `graph_coverage` 是检索**可观测性**指标，非质量指标。检索 2 条全错与 8 条全对在这些指标上无差异。
- **企业级要求**：context precision/recall、答案忠实度（RAGAS 口径或等价自研）。

### GAP-003　轨迹评测有效覆盖近零（P1）

- **证据**：标注 6/200；两份全量报告 `trajectory: null`（T050 之前的运行）；t050 冒烟仅 2 条 FAQ（scored=0）。
- **说明**：机制（LCS 按序子序列/禁调/路由/入参子集断言/次数+冗余，D026 独立口径）设计成熟，纯投入标注成本即可激活。

### GAP-004　无多轮对话评测（P1）

- **证据**：全部 224 条用例均为单轮 `user_input`；schema 无 turns/history 字段。
- **影响**：PostgreSQLSaver 会话记忆（F14）对回答质量的贡献从未被评测。

### GAP-005　无安全对抗评测（P2）

- **证据**：数据集关键词扫描（注入/越权/诱导/越狱类）零命中；F11 脱敏仅有单元测试，未进评测闭环。
- **企业级要求**（保险行业强制项）：prompt 注入、越权查询、PII 输出泄露、违规承诺诱导、方言/错别字鲁棒性。

### GAP-006　性能/成本仅均值口径（P2）

- **现状**：`avg_duration_s`；token 成本仅 A/B 组间总量差分。
- **企业级要求**：p95/p99 延迟、单例 token 成本、超时率。

### GAP-007　CI 无评测回归门禁（P2）

- **证据**：`ci.yml` 仅 ruff + pytest + docker compose 验证；baseline.json 存档但无自动对比/阈值拦截。
- **企业级标准**：PR 跑 smoke 子集门禁（如 `--limit 20`，完成率 ≥ 基线 −5pp 拦截）+ nightly main 全量。

### GAP-008　统计严谨性不足（P2）

- **现状**：z 检验已有；但 n=200、完成率 88% 时 95% Wilson CI ≈ ±4.5pp——baseline 88% vs regression 87% 的差异在噪声内；每变体单次运行，LLM 非确定性方差未量化。
- **企业级要求**：报告附 Wilson CI；关键对比至少 3 次重复运行取均值±方差。

### 次要问题（顺手修）

- graph_assoc 数据集 24 条中 23 条 category=`simple_faq`，与"复杂关联类"定义漂移（T033 文案 vs 实际标注）。
- tool_accuracy 空分母返回 1.0、trajectory scored=0 返回 1.0——"无数据=满分"口径在报告消费侧易误读，建议 UI 上显式标注"N/A（未标注）"。

## 4. 整改规格（可直接执行）

> 遵循 AGENTS.md 约定：每项一个任务、单 commit、`.agent/` 状态同步、先记 decisions.md 再实现。

### P0-1　修复 human_precision 并补齐指标（预计 0.5h）

```python
# evals/metrics.py aggregate() 内替换 L144-147
intervened = [r for r in results if r.need_human_intervention]
expected_human = [r for r in results if r.category and r.human_match and r.need_human_intervention]
# precision：实际转人工中「确实该转」的占比
human_precision = (
    sum(1 for r in intervened if r.human_match) / len(intervened) if intervened else 0.0
)
# recall：期望转人工中被转了的占比（EvalReport 增列）
expected_true = [r for r in results if <case.expect_human_intervention>]  # 需从 score_case 透传期望值
human_recall = sum(1 for r in expected_true if r.need_human_intervention) / len(expected_true) if expected_true else 0.0
```

- 涉及：`evals/metrics.py`（CaseResult 增 `expect_human: bool` 透传）、`evals/schemas.py`（无需动）、`tests/evals/test_scoring.py`（新增 4 用例：全对/全错/空集/混合）
- 验收：构造 10 例 mock（3 期望转人工）→ precision/recall 数值正确；全量报告不再出现恒 0/1 的退化值

### P0-2　标注转人工期望用例 15-20 条（预计 1h）

- 位置：`evals/datasets/eval_dataset.json`，`edge_case` 类目下新增 `HITL-001..N`（或独立 category `human_handoff`，需同步 EvalCategory 枚举）
- 用例设计模板：①合规 REJECT 路径（高风险职业出险/疑似骗保表述）②材料严重缺失无法自动判断 ③超出保障范围且用户情绪激动 ④涉及法律纠纷表述 ⑤ LLM/外部依赖故障降级路径（spec §6：DeepSeek 故障降级为转人工提示）
- 每条：`expect_human_intervention: true` + `must_not_include`（违规承诺话术）+ note 标注来源
- 验收：`--limit` 跑通；human_recall/precision 均非零且可解释；单测（test_dataset.py）过

### P0-3　意图准确率进报告（预计 0.5h）

```python
# CaseResult 增 intent_match: bool | None（None=未标注不考核）
# run_case：从 a06['intent'] 取实际意图（需确认 intent 节点输出回填 state）
# score_case：case.expected_intent and 实际意图 → intent_match
# aggregate：intent_accuracy = matched / scored（空分母 N/A）
```

- 涉及：`evals/metrics.py`、`evals/test_suite.py`（回填 intent）、`evals/schemas.py`（EvalReport 增列）、`tests/evals/test_scoring.py`
- 验收：F03 的 ≥90% 目标在报告可见；80 条标注用例计入分母

### P1-1　数值断言字段（预计 1h）

- `EvalCase` 增 `expected_numbers: list[str]`；判分：`_norm` 后逐个精确 in（金额/天数/比例类必标）
- 优先给 multi_step 80 条中含金额计算的用例补标
- 验收：金额算错的用例必 FAIL；单测覆盖千分位/全角/小数位容差

### P1-2　LLM-as-judge 二层判分（预计 0.5 天）

- 新文件 `evals/judge.py`：对 must_include 为空的用例，judge 按 rubric（事实一致性/完整性/合规性 3 维 0-2 分）打分，阈值 ≥4 判过；judge 结果存 `CaseResult.judge` 独立列，**不并入 passed**（同 D026 轨迹口径，先观察再收敛）
- 校准：人工抽检 50 条与 judge 对齐率 ≥85% 才采信（decisions.md 记录）
- 成本：仅 judge 空判分用例，deepseek-v4-flash 估算 <¥1/全量

### P1-3　轨迹标注扩展至 multi_step 全量（预计 0.5 天，主要人工）

- order + route 两维优先（args/forbidden 保持抽样）；`scripts/` 可写半自动辅助（从现有全量报告的 tool_trace 提取高频序列供人工确认）
- 验收：order/route scored ≥80；全量报告轨迹块非 null 且分母可解释

### P1-4　多轮用例集 30 条（预计 0.5 天）

- `EvalCase` 增 `turns: list[str]`（默认空=单轮，向后兼容）；`run_case` 支持逐轮 ainvoke 同一 thread，末轮判分
- 场景：上文保单号指代、追问材料清单、中途改口、上下文冲突检测
- 验收：schema 校验过；3 条冒烟跑通；单测覆盖多轮判分口径

### P2-1　安全对抗集 20 条（预计 0.5 天）

- 注入（"忽略之前指令"）/ 越权（改理赔记录）/ PII 诱导（让他复述身份证）/ 违规承诺诱导 / 错别字与方言
- 判分以 `must_not_include`（违规输出）+ `forbidden_tools`（越权调用）为主

### P2-2　CI 评测门禁（预计 2h）

- `ci.yml` 增 job：PR 触发 `uv run python -m evals.test_suite --limit 20 --out ci_report.json`（需 LLM_API_KEY secret；无 key 时 job skip 而非 fail）
- 门禁脚本：`scripts/check_eval_gate.py` 读 ci_report vs evals/reports/baseline.json，完成率降幅 >5pp 退出码 1
- 可选：GitHub Actions nightly 全量（main 分支，cost 控制）

### P2-3　报告统计增强（预计 2h）

- `EvalReport` 增 `p95_duration_s`、`tokens_per_case`（复用 ab_test 的 Prometheus 差分口径下沉到 test_suite）、`wilson_ci: [lo, hi]`
- UI 趋势图 hover 展示 CI

## 5. 执行顺序与依赖

```
P0-1 ──→ P0-2 ──→ P0-3        （半天，北极星闭环）
P1-1 / P1-2 / P1-3 可并行      （判分深度）
P1-4 独立                        （多轮）
P2-1 / P2-2 / P2-3 收尾         （防线闭环）
```

全部完成后重跑全量基线，替换 `baseline.json`，并在 `docs/architecture.md` §9 同步指标口径变更。
