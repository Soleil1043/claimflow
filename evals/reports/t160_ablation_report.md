# T160 单 Agent 基线消融报告（同 153 案，双侧全 LLM 真跑）

> 问题：Orchestrator-Worker 拆分相对"一个 Agent 拿全部工具独立办案"值多少？
> 口径（D074）：同主门 153 案、同模型 deepseek-flash、同判分（score_case 五维），
> 双侧全 LLM 真跑。多 Agent 侧 = 评测门 `--llm --llm-workers`（与生产
> create_default_case_graph 同构，确定性守卫/公式/静态合规门保留——它们是架构的一部分）；
> 单 Agent 侧 = `evals/single_agent_baseline.py`（create_agent 单体 + 11 个 worker 工具 +
> SingleAgentOutput 结构化输出，材料清单随指令直给，模型调用上限 16）。

## 一、总表

| 维度 | 多 Agent（Orchestrator-Worker） | 单 Agent（基线） | 差异 |
|---|---|---|---|
| 总一致率 | **88.9%**（136/153） | 60.8%（93/153） | **-28.1pp** |
| 路由一致率 | 88.9% | 72.5% | -16.4pp |
| 金额正确率（硬门） | **100%** | 88.9%（17 案错） | 单 Agent 失守金额硬门 |
| 责任一致率 | 100% | 83.7% | -16.3pp |
| 险种分类 | 100% | 100% | 持平 |
| 红线漏放 | 0（静态合规门，图结构保证） | 0（后置 check_text 复查） | 持平* |
| tokens/案 | 13,670 | 23,272 | **+70%** |
| LLM 调用/案 | 7.8（分布于 6 worker，可并行） | 5.0（串行上下文） | 单 Agent 次数少但上下文膨胀 |
| 工具调用/案 | —（按 worker 分摊） | 8.6 | — |
| 时长/案（并发 8 观测） | 11.87s（P95 15.16） | 7.57s（P95 10.69） | 单 Agent 调用次数少故单案略快 |

\* 单 Agent 红线为 0 依赖两件事：prompt 里写死红线纪律 + 工具集里有
`compliance_rule_check` 可自检。多 Agent 的红线 0 是**图结构保证**（决定书生成后
必须穿过静态合规门才能出图，路由决策不可绕过）——前者是"这次没漏"，后者是"漏不了"。

## 二、失败模式对比（消融的核心发现）

**多 Agent 侧（17 例，全部 normal 案，方向一律过严）**：
- 全部形态为 auto → human：责任 LLM 判定结论正确（covered），但置信度低于
  `auto_approve_confidence_floor=0.8`（实测 0.72）→ 分级自动门按设计拒绝自动签发转人工。
- 即：**失败方向全部是 fail-safe**——确定性公式节点保证金额 100%，守卫拦截 100%，
  静态合规门红线 0，置信度不足宁可转人工不放水。rejected/partial/fraud/edge/missing/
  intake 六类 94 案全部一致。

**单 Agent 侧（60 例，双向失守）**：
- **partial 理算 17/17 全灭**：部分赔付需要"可赔基数 × 比例 - 免赔额"的 Decimal
  精确公式；单 Agent 即便调用 claim_calculator，仍出现不采信工具结果/比例取错/
  免赔额漏扣——多 Agent 侧这是纯确定性节点（amount_calc），结构上不可能错。
- **27 例错误 auto**：应转人工/补件的案被直接签发——单 Agent 没有前置条件守卫、
  材料置信度门（floor 0.6）、auto_approve 置信度门（floor 0.8）三道确定性防线，
  "低置信度宁可转人工"的保守性消失。
- **12 例 missing 案未转补件**：材料缺失的识别与补件流转（supplement interrupt）
  是多 Agent 图里的显式状态机路径；单 Agent 需自行从 prompt 里推断。

## 三、结论

1. **一致率 -28.1pp、金额硬门失守、错误方向从"过严"变"过松"**：拆分的价值不是
   "多个人脑"，而是把"判断交给数据"的部分（公式/守卫/合规门/置信度门）从概率性
   的模型上下文里拿出去，用图结构钉死（D039）。
2. **token 成本 +70%**：单 Agent 把六阶段全部中间结果（条款 RAG 片段、保单、风控）
   塞进同一上下文，每步调用都重复携带；多 Agent 各 worker 上下文短小、天然隔离。
3. 单 Agent 唯一优势是单案墙钟（调用次数 5.0 vs 7.8）——但多 Agent 的 worker 并行
   在吞吐上的优势、以及按阶段独立演进（skill 文本迭代不改代码）的能力，二者不构成对价。
4. 多 Agent 全 LLM 形态自身的已知边界同样如实呈现：LLM 调度存在保守倾向
   （17 例低置信度转人工，路由软门 88.9% < 95%）；生产口径下这正是分级自动设计
   的预期行为（不确定 → 人），且金额/红线/守卫三道硬门零失守。

## 四、复现

```bash
# 多 Agent 侧（评测门全量 LLM 形态）
uv run python -m evals.adjudication_suite --llm --llm-workers --concurrency 8 \
    --out evals/reports/t160_multi_agent_llm_153.json
# 单 Agent 侧
uv run python -m evals.single_agent_baseline --concurrency 8 \
    --out evals/reports/t160_single_agent_baseline.json
```

原始报告：`evals/reports/t160_multi_agent_llm_153.json`、
`evals/reports/t160_single_agent_baseline.json`（含逐案判分、token、调用数）。
口径备注：LLM 生成存在运行间方差，本报告为 2026-10-07 单次双侧真跑快照；
sequence 维度对单 Agent N/A（无派发序列），不计入 matched。
