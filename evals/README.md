# evals/ —— 金样本评测门

评测即门禁：CI 每次跑确定性全量，六门全绿才过。门禁阈值与运行时配置同源
（`schemas/contract.py`）——**评测门 = 运行时门，不会漂移**。

## 文件清单

| 文件 | 职责 |
|------|------|
| `adjudication_suite.py` | 运行器：确定性 / `--llm` 双模式；`--dataset adversarial` 切对抗门；`--offset/--out` 分片 |
| `gates.py` | 六门纯函数（金额 / 红线 / 守卫 / 路由 / 责任 / 预算 + 对抗 tier） |
| `adjudication_metrics.py` | 判分：路由一致 / 金额 Decimal / 责任 / 调度序列 |
| `schemas.py` | Adjudication* 数据模型 |

## 数据集

| 数据集 | 规模 | 覆盖 |
|--------|------|------|
| `datasets/adjudication.json` | 153 案 | 七类（正常/拒赔/部分责任/风控/边界/缺件/受理分类）× 四险种，期望金额精确到分 |
| `datasets/adjudication_adversarial.json` | 14 案 | injection 8（注入/诱导/PII…）+ robustness 6（除外同义词），独立基线不污染主门 |

## 运行

```bash
uv run python -m evals.adjudication_suite                       # 主门（确定性，零 LLM）
uv run python -m evals.adjudication_suite --llm                 # 真实 LLM 调度全链
uv run python -m evals.adjudication_suite --dataset adversarial # 对抗门
uv run python -m evals.adjudication_suite --limit 20            # 子集冒烟
```

## 当前基线

主门 153/153 与 LLM 模式 153/153 双百、对抗门 14/14 双模式全绿、失败集 0。
历史报告在 `reports/`（v1 时期已归档 `reports/archive/`）。
架构见 [`../docs/architecture.md`](../docs/architecture.md) §14。
