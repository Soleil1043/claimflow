# Prompt 缓存未命中问题 · 完整事件报告

> 报告日期：2026-10-09　模型：deepseek-flash（api.deepseek.com）　涉及任务：T162 → T163 → **T164** → T165
> 指标：`claimflow_llm_cache_tokens_total{model, stage, result}`
> 报告性质：完整事件复盘（发现 → 误判 → 纠偏 → 解决 → 沉淀），非单纯技术报告

---

## 摘要（一句话）

一个「缓存全 miss」的现象，暴露了两个独立缺陷（**观测缺口** + **prompt 布局缺陷**）；过程中出现了一次**归因错误**，最终靠 stage 维度纠偏；修复后路由命中率 **0% → 86.8%**，整体命中率 **82.4%**。

**报告的核心价值不在 86.8%，在于「观测缺口会伪造归因」这条教训。**

---

## 事件时间线

| 阶段 | 任务 | 时点 | 发生了什么 |
|---|---|---|---|
| ① 埋点 | T162 | 10-08 20:00 | 加缓存指标 `claimflow_llm_cache_tokens_total`，定位 DeepSeek 非标字段的存活路径 |
| ② 发现 | T163 | 10-08 22:00 | 面板上线 + 真实 key 跑案件 → **1845 miss / 0 hit** |
| ③ 误判 | T163 | 10-08 22:30 | 判定「根因 = `build_system_prompt`拼接顺序把缓存窗口压在快照里」 |
| ④ 方案 | — | 10-09 02:00 | 记 D076：三层修复方案对比，选「先观测后布局」（C） |
| ⑤ 纠偏 | T164 | 10-09 02:30 | **补齐观测后发现：1845 miss 实为材料提取数据，四个调用点从未被观测** |
| ⑥ 修复 | T165 | 10-09 03:00 | 三 prompt 模板删动态占位段 → 路由命中率 **0% → 86.8%** |
| ⑦ 补短| T166 | 10-09 14:40 | Prometheus retention 30d + OTel 采样 0.2（缓存明细可按 trace 查） |
| ⑧ 沉淀 | T167-T169 | 10-09 15:00-20:00 | 文档全量刷新 + 问题追踪表 P019 单列此教训 |

---

## 一、发现：1845 miss / 0 hit

### 1.1 现象

带真实 key 跑端到端案件（`verify_adjudication` 11 次 LLM 调用），缓存指标全miss：

```
claimflow_llm_cache_tokens_total{model="deepseek-flash",result="miss"} 1845.0
claimflow_llm_cache_tokens_total{model="deepseek-flash",result="hit"}  0.0
```

**零命中。** 而同一时刻的对照实验却正常：

| 场景 | 稳态命中率 |
|---|---|
| 纯稳定前缀（1339 字符，模拟 skill 规程规模） | **77.8%**（640 hit / 183 miss） |
| 同 prompt 重复调用（路由 prompt + 快照，三档快照 99/504/990 字） | **67.5% ~ 77.1%** |

### 1.2 由此排除的三个可能

| 假设 | 排除依据 |
|---|---|
| 指标埋点失效 | 对照实验正常出数（77.8%），提取链路在真实 API 上验证通过 |
| DeepSeek 缓存没开| 对照实验有77.8% 命中，说明服务端缓存正常 |
| 抽取字段不对 | 同上，非标字段提取路径已被探针锁定 |

**结论锁定在 prompt 形态本身。**

---

## 二、误判：归因错了

### 2.1 当时的结论

> **根因（已修正）：拼接顺序把缓存窗口压在快照里。**
> `build_system_prompt` 先 `base_prompt.format(**fmt)` 把每案唯一快照填进占位符，再把静态 skill 规程拼在快照之后——prompt 实际形态为 `[短稳定前缀][每案唯一快照][静态规程]`，DeepSeek 前缀缓存的分叉点恰落在唯一数据开头，其后全部 miss。

配套结论：把快照移出 system 走 user message 即可解决。

### 2.2 这个结论错在哪

**三个调用点的数据当时根本没被观测到。**

`observed_ainvoke`（T039 起唯一的 LLM 埋点入口）只覆盖**材料提取**一处。四个关键调用点是裸调：

| 调用点 | 埋点状态（10-08 时） |
|---|---|
| ocr 材料提取 | ✅ 经 `observed_ainvoke` |
| orchestrator 路由 | ❌ 裸调 |
| material_review AI 审查 | ❌ 裸调 |
| decision 叙述 | ❌ 裸调 |
| liability_judge（create_agent 子图） | ❌ 子图内部调用，不经主入口 |

于是 1845 miss **全部来自材料提取**——prompt 主体就是那份唯一文档内容，前缀天然极短，miss 属**预期行为**。

### 2.3 两个缺陷其实是两件事

| | 观测缺口 | 布局缺陷 |
|---|---|---|
| 表现 | 四个调用点命中率**未知** | 已知有问题的点命中率**确实低** |
| 修复手段 | 加指标 + stage 维度 | 改 prompt 结构 |
| 是否需要对方才能验收 | 需要——没基线就无法判断改完有没有提升 | — |

**原结论把「材料提取的天然 miss」当成了「路由的布局缺陷」，等于拿错了尺子量错了对象。**

---

## 三、纠偏：补齐观测后真相浮现

T164 补齐 stage 维度 + 四个裸调点接入后，真相：

| 调用点 | T163 误判 | T164 实测 | 性质 |
|---|---|---|---|
| ocr 材料提取 | 全 miss，归咎布局 | 1845 miss / 0 hit | **天然 miss，非缺陷** |
| orchestrator 路由 | 全 miss，归咎布局 | 0%（未被观测） | **布局缺陷（待修）** |
| liability_judge | 未提及 | **hit 4352 / miss 1608 = 73.0%** | system 本就纯静态，天然高命中 |
| material_review | 未提及 | 0%（未被观测） | 布局缺陷（待修） |
| decision 叙述 | 未提及 | 0%（未被观测） | 布局缺陷（待修） |

**关键反证**：`liability_judge` 命中率 73% ——如果"布局是根因"成立，这个 system 同样含大量 skill 规程文本的调用点不该这么高。**73% 这个数字直接证伪了原结论。**

### 3.1 纠偏为什么能发生

不是运气。是 T164 先做了两件事：

1. **stage 维度**——把混在一起的 1845 miss 拆成可归属的series
2. **统一入口 + 回调 handler**——create_agent 子图经`config.callbacks` 传播观测（实测 langgraph 会把 callbacks 传到节点内 chat model）

### 3.2 教训（已写入 progress.md 的 P019）

> **指标覆盖不全时，「某处指标异常」推不出因果。**
>
> 观测缺口的危险不在于「看不见」，在于「让错误归因看起来有数据支撑」——1845 miss 是个精确的数字，比"感觉哪里不对"更容易让人信。

---

## 四、解决：分层修复（T164 → T165）

### 4.1 方案选择（D076）

| 选项 | 判断 |
|---|---|
| A. 只改布局 | ✗ 改完无法验证效果（无分stage 基线），也无法区分「布局收益」与「天然 miss」 |
| B. 只补观测 | ✗ 拿到数据但不动布局，命中率不会变 |
| **C. 先观测后布局** | ✅ 选中：before/after 数据闭环 |

**理由**：改布局的验收标准就是命中率变化，没有基线就没有验收。

### 4.2 T164 观测补全

| 改动 | 内容 |
|---|---|
| metrics | `LLM_CACHE_TOKENS` 加 `stage` label（model×stage×result）；`record_llm_call` 加 `stage` 参数（默认 `other` 向后兼容） |
| observed_ainvoke | 泛化接受 `with_structured_output` 包装 Runnable（`_model_name` 沿 `.bound`/`.first` 递归探测内层） |
| 三处裸调 | orchestrator 路由 / material_review AI 审查 / decision 叙述接入 `phase_ainvoke` |
| create_agent 子图 | 新增 `LlmUsageCallbackHandler`，从 `on_llm_end` 的 `llm_output.token_usage` 取值 |
| Grafana | 面板 11/12/14 加 stage 维度 |

### 4.3 T165 布局改造

**根因**：`build_system_prompt` 的形态是 `[短稳定前缀][每案唯一数据][静态规程]`——**分叉点落在唯一数据开头**。

**改法**：三 prompt 模板删动态占位段，三节点改双消息：

```
[SystemMessage]  全静态（规程 + 原则 + skill 文本）← 逐字稳定，整段命中缓存
[HumanMessage]   每案唯一数据 + <<<DATA…>>>DATA 定界
```

| 模板 | 删除的动态占位段 |
|---|---|
| `CASE_ORCHESTRATOR_ROUTING_PROMPT` | `## 案件快照 <<<DATA {snapshot} >>>DATA` |
| `MATERIAL_REVIEW_AI_PROMPT` | `## 提取结果 {documents}` |
| `DECISION_NARRATIVE_PROMPT` | `## 事实 {facts}` |

`<<<DATA…>>>` 定界随数据移入 user message，system 保留数据边界铁律文字并改为指向"用户消息"。

### 4.4 结果

| 调用点 | 改造前 | 改造后 | 性质 |
|---|---|---|---|
| **orchestrator 路由** | 0% | **86.8%** | 布局缺陷，已修 |
| **material_review 审查** | 0% | **75.2%** | 布局缺陷，已修 |
| **decision 叙述** | 0% | **59.5%** | 布局缺陷，已修 |
| liability_judge | — | 73.0% | 静态 system，天然高命中 |
| ocr 材料提取 | 0% | 0% | **天然 miss，非缺陷** |
| **整体（Grafana 实测）** | — | **82.4%** | — |

路由三轮实测：**86.7% → 86.8% → 86.8%**。快照各异而命中率恒定 = system 前缀逐字一致（不是碰巧）。

---

## 五、验收

### 5.1 功能零退化

| 项 | 结果 |
|---|---|
| 单元测试 | **528 passed** + ruff 绿 |
| 确定性主门 | 153 案六门全绿，一致率 100% |
| **注入对抗（确定性）** | **8/8 全绿** |
| **注入对抗（LLM 档）** | **8/8 全绿**，一致率 100% |
| 30 案 LLM 评测 | 六门全绿，一致率 100% |

**注入防线未因布局调整而削弱**——`<<<DATA…>>>` 定界只是移了位置，模型看到的「先规则后数据」顺序反而更清晰（system=指令区、user=数据区）。

### 5.2 成本收益

| 项 | 改造前 | 改造后 |
|---|---|---|
| tokens/案 | 7,802 | 7,659（prompt 变短） |

**token 数几乎没变，收益在计费折扣**：命中部分按 1/10 计价。以路由为例，单次调用约 3,700 token，其中 3,260 走缓存——原本全价，现在按 1/10 付。

---

## 六、过程中踩的坑

| 编号 | 坑 | 解法 |
|---|---|---|
| 1 | LangChain 的 `usage_metadata` **丢弃** DeepSeek 非标缓存字段（`_create_usage_metadata` 只映射标准字段） | 改从 `response_metadata["token_usage"]` 提取（探针实测唯一存活路径） |
| 2 | `with_structured_output` 返回**纯 Pydantic 对象**，无 `usage_metadata` / `response_metadata` | usage 只能经 `on_llm_end` 的 `llm_output` 取；判据用「是否自带模型身份属性」而非 `isinstance(BaseChatModel)` |
| 3 | 验证 langgraph 回调传播时，假模型探针因恒返回 tool_call 陷入无限循环 | 改真实模型 `invoke_worker` 验证 |
| 4 | Grafana 面板引用不存在的指标 → **静默 No data** 不报错 | 写面板前核对 `metrics.py` 的指标名/类型/labelnames |
| 5 | 容器跑旧镜像，`/metrics` 查不到新指标 | `docker compose build app`；确诊手法 `exec app grep -c 指标名 /app/路径` 返回 0 |

---

## 七、沉淀（已写入项目文档）

| 文件 | 沉淀内容 |
|---|---|
| `AGENTS.md` §3.6 | **Prompt 硬约束**：system 必须全静态逐字稳定，动态数据一律走 user message + DATA 定界 |
| `AGENTS.md` §1 第 8 条 | 设计变动先算账（本次是「先观测后布局」的分层决策） |
| `services/README.md` | Prompt 布局约定 + `build_system_prompt` 的静态要求 |
| `skills/README.md` | 「规程进 system（逐字稳定）、案件数据进 user message」及其原因 |
| `.agent/progress.md` P019 | 观测缺口伪造因果的教训（问题追踪表单列） |
| `.agent/decisions.md` D076 | 三层修复方案对比与选择理由 |
| `docs/architecture.md` §13 | 观测三层 + 缓存提取链路 + 布局改造实测对照表 |
| `prometheus/README.md` / `otelcol/README.md` | retention 30d /采样率 0.2 / 缓存原值查 Jaeger |

---

## 八、遗留与建议

### 8.1 未做

| 项 | 原因 | 建议 |
|---|---|---|
| ocr 材料提取的缓存优化 | prompt 主体即唯一文档，前缀天然短 | **不建议优化**——即使把静态规程前置，文档内容仍会分叉。这是形态决定的，不是缺陷 |
| decision 叙述 59.5%（三者最低） | 该 prompt 本身较短，system 前缀占比小 | 若要再提升，可考虑把「要求」段拆更细，但收益有限 |
| 缓存命中率入Alerts | 目前只有 dashboard 视图 | 若命中率跌破 50% 应告警——但需先有稳定基线（现在有了：82.4%） |
| 容器日志持久化 | 属另一议题（T166 时提出） | 需要时再配logging driver |

### 8.2 方法论沉淀

> **观测缺口会让错误归因看起来有数据支撑。**
>
> 本事件里 1845 miss 是个精确的数字，比"感觉哪里不对"更容易让人信——而它恰恰是错的。stage 维度不只是为了看得更细，更是为了让「哪条数据属于谁」变得可追溯。
>
> 推广：任何指标体系设计，都应先问「这个数字能不能归属到具体调用点」，否则聚合值既不能诊断也不能验收。

---

## 附：相关文件索引

| 文件 | 作用 |
|---|---|
| `services/observability/metrics.py` | 缓存指标定义（model×stage×result） |
| `services/observability/llm_metrics.py` | 三来源提取 + 结构化输出回调分支 + `LlmUsageCallbackHandler` |
| `scripts/probe_deepseek_cache_usage.py` | 缓存字段存活探针（全 mock，免 Key） |
| `scripts/measure_cache_hit_rate.py` | 真实命中率测量（需真 Key） |
| `evals/reports/t163_cache_hit_report.md` | T163 原始测量报告（工程视角） |
| `.agent/decisions.md` D076 | 决策链 |
| `.agent/progress.md` P019 | 教训追踪 |

## 附：容器内累计实测数据（`/metrics` 原始输出）

T166 启用 monitoring+tracing 全栈后跑真实案件累积，`http://localhost:8000/metrics` 抓取：

```
claimflow_llm_cache_tokens_total{model="deepseek-flash",result="hit", stage="orchestrator"}      14208.0
claimflow_llm_cache_tokens_total{model="deepseek-flash",result="miss",stage="orchestrator"}       2334.0   → 85.9%
claimflow_llm_cache_tokens_total{model="deepseek-flash",result="hit", stage="material_review"}     5504.0
claimflow_llm_cache_tokens_total{model="deepseek-flash",result="miss",stage="material_review"}     1214.0   → 81.9%
claimflow_llm_cache_tokens_total{model="deepseek-flash",result="hit", stage="liability_judge"}     3712.0
claimflow_llm_cache_tokens_total{model="deepseek-flash",result="miss",stage="liability_judge"}467.0   → 88.8%
claimflow_llm_cache_tokens_total{model="deepseek-flash",result="hit", stage="ocr"}                    0.0
claimflow_llm_cache_tokens_total{model="deepseek-flash",result="miss",stage="ocr"}                1845.0   → 0%（天然 miss）
```

**注意 `ocr` 的 1845 miss 与 T163 发现时完全一致**——这三个数字一字未变，是「T163 归因错误」最硬的证据：观测缺口补齐后回看，那批数据从头到尾都属于材料提取，与路由无关。

整体：hit 23,424 / miss 5,860 = **80.0%**（Grafana 按 `increase[$__range]` 口径算得 82.4%，差异来自统计窗口起点不同，两者互相印证）。

单调用级原值见 Jaeger `:16686`（Counter 已聚合，此处是唯一能看单次 hit/miss 的入口）：
```
gen_ai.usage.cache_read_tokens = 896 / 1024 / 1280
gen_ai.usage.cache_miss_tokens = 166 / 167 / 168 / 170 / 212 / 215 / 245
```
