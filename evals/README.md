# evals/ —— 金样本评测门

评测即门禁：CI 每次跑确定性全量，六门全绿才过。门禁阈值与运行时配置同源
（`schemas/contract.py`）——**评测门 = 运行时门，不会漂移**。

## 文件清单

| 文件 | 职责 |
|------|------|
| `adjudication_suite.py` | 运行器：确定性 / `--llm` 双模式；`--dataset adversarial`切注入门、`adversarial_holdout` 切盲测集；`--offset/--out/--limit/--concurrency` 分片并发；`--llm-workers` 与生产同构（T160） |
| `adjudication_harness.py` | 临时库装配 / 种子 / 观测提取 / 报告装配（T153 从suite 拆出） |
| `gates.py` | 六门纯函数（金额 / 红线 / 守卫 / 路由 / 责任 / 预算 + 对抗 tier） |
| `adjudication_metrics.py` | 判分：路由一致 / 金额 Decimal / 责任 / 调度序列 |
| `redteam.py` | 红队变异器：10 类变异（分类法对齐 Garak promptinject / PyRIT converters-attacks，**带 framework_ref 溯源**——自研不引框架本体，探测对象语义错位 + 依赖重，T159） |
| `single_agent_baseline.py` | 单 Agent 消融运行器（create_agent 单体 + 全工具集 + 结构化输出，与多 Agent 同口径判分，T160） |
| `rag_metrics.py` | RAGAS 四指标自实现（context_precision/recall、faithfulness、answer_relevancy）+ Recall@K / MRR（T161） |
| `schemas.py` | Adjudication* / SingleAgentOutput 数据模型 |

## 数据集

| 数据集 | 规模 | 覆盖 | 门禁位置 |
|--------|------|------|---------|
| `datasets/adjudication.json` | 153 案 | 七类（正常/拒赔/部分责任/风控/边界/缺件/受理分类）× 四险种，期望金额精确到分 | push CI 主门 |
| `datasets/adjudication_adversarial.json` | 8 案 | injection 8（注入/角色伪装/虚构免责/红线诱导/PII/施压/伪造/字段注入） | **push CI 硬门** |
| `datasets/adjudication_adversarial_holdout.json` | 30 案 | robustness 6 seed + 24 红队变体 | **nightly 盲测**（不进 push） |
| `datasets/rag_qa_liability.json` | 24 对 | 责任认定 RAG 问答（gold = source_file + 标记子串，单测逐字校验存在） | T161 评测 |

**为什么拆两份**（T159）：同义词鲁棒性走关键词路径，最易对fixture 过拟合——留在
push CI 里会给"过了"一个假信号。盲测集才反映真实泛化。

## 运行

```bash
uv run python -m evals.adjudication_suite                          # 主门 153 案（确定性，零 LLM）
uv run python -m evals.adjudication_suite --llm                    # 真实 LLM 调度全链
uv run python -m evals.adjudication_suite --dataset adversarial    # 注入硬门 8 案
uv run python -m evals.adjudication_suite --dataset adversarial_holdout  # 盲测 30 案
uv run python -m evals.adjudication_suite --limit 30 --concurrency 3     # LLM 档分片
uv run python -m evals.single_agent_baseline                       # 单 Agent 消融对照
uv run python -m scripts.redteam_adversarial --probe               # 红队变体实跑判定
uv run python scripts/eval_rag.py                                  # RAG Recall@K + RAGAS 四指标
```

## 当前基线

| 项 | 数值 |
|---|---|
| 主门（确定性与LLM 双模式） | 153/153 = 100%，六门全绿 |
| 注入硬门（确定性 + LLM） | 8/8 双模式全绿 |
| 红队 hold-out 盲测 | 30/30 全绿（8 seed × 10 变异器 + 6 原始 robustness） |
| 单 Agent 消融（T160） | 多 Agent 88.9% vs 单 Agent 60.8%；金额硬门失守 88.9%；token +70% |
| RAG（T161） | Recall@1=54% / @2=79% / @4=96% / @8=100%；MRR 0.724；RAGAS CP 0.868 / CR 0.806 / FA 0.929 / AR 0.909 |
| 调度成本 | 7,659 tokens/案，4.4 次 LLM 调用/案 |

失败集 0。历史报告在 `reports/`（v1 时期已归档 `reports/archive/`），
红队证据归档 `reports/t159_redteam_evidence.md`，消融报告 `reports/t160_ablation_report.md`。

架构见 [`../docs/architecture.md`](../docs/architecture.md) §14。
