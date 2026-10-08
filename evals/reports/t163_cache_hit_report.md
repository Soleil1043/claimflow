# T163 · DeepSeek Prompt Caching 真实命中率测量报告

> 日期：2026-10-08　模型：deepseek-flash（api.deepseek.com）　工具：scripts/measure_cache_hit_rate.py + 核赔冒烟
> 指标：`claimflow_llm_cache_tokens_total{model,result}`（T162）

---

## 一、结论速览

| 场景 | 稳态命中率 | 说明 |
|---|---|---|
| 纯稳定前缀（1339 字符，模拟 skill 规程规模） | **77.8%** | 640 hit / 183 miss，T162 提取链路在真实 API 上验证通过 |
| 同prompt 重复调用（路由 prompt + 快照） | **67.5% ~ 77.1%** | 快照 99 / 504 / 990 字三档，均稳定命中 |
| **核赔真实案件（verify_adjudication 11 次调用）** | **0%（全 miss）** | 1845 miss / 0 hit |

**这不是指标失效，是核赔 prompt 的天然形态决定的。** 根因见下。

---

## 二、真实案件为什么全 miss

### 直接证据

```
claimflow_llm_calls_total{model="deepseek-flash",status="success"} 11.0
claimflow_llm_tokens_total{kind="prompt",model="deepseek-flash"} 1845.0   # 均 168 tok/次
claimflow_llm_cache_tokens_total{result="hit"}  0.0
claimflow_llm_cache_tokens_total{result="miss"} 1845.0
```

> ⚠️ **2026-10-09 归因纠偏（T164 实锤）**：本报告把1845 miss 全归给路由布局是**错的**。
> 该批数据来自**材料提取**（ocr）——prompt 主体即唯一文档内容，天然 miss 属预期。
> 路由 / 材料审查 / 叙述 / 责任认定四个调用点当时**从未被观测**（裸调不经 observed_ainvoke）。
> 真实布局缺陷由 T165 修复，实测路由命中率 0% → 86.8%。详见下节。

### 根因（已修正）：拼接顺序把缓存窗口压在快照里

`services/skills.py::build_system_prompt` 的拼接逻辑（line 47-55）：

```python
content = base_prompt.format(**fmt)   # fmt 里已填入 snapshot（每案唯一，最长 3000 字符）
skill = load_skill(stage, line)       # 再把作业规程拼在后面
if skill:
    content = f"{content.rstrip()}\n\n# 作业规程（skill）\n\n{skill}"
```

而 base prompt 本身的布局是（`services/llm/prompts.py`）：

```
你是保险理赔智能核赔平台的调度 Orchestrator…
## 可派发目标 …## 调度原则 …## 数据边界 …
## 案件快照
<<<DATA
{snapshot}      ← 每案唯一，3000 字符截断，占 prompt 主体
>>>DATA
```

于是每案的 system prompt实际形态是：

```
[~500 tok 稳定前缀][~1500 tok 每案唯一快照][skill 规程]
                    ↑ DeepSeek 缓存只覆盖到这里
```

**缓存窗口恰好落在唯一数据上 → 每次前缀都不同 → 必然全 miss。**

### 对照实验：同一 prompt 重复调用则正常命中

| 快照长度 | 稳定前缀命中 | 未命中 | 命中率 |
|---|---|---|---|
| 99 字 | 384 | 185 | 67.5% |
| 504 字 | 640 | 190 | 77.1% |
| 990 字 | 768 | 248 | 75.6% |

同一份 prompt 连打三次，第三轮稳态命中 67-77%——**证明提取链路与 DeepSeek 缓存机制均正常**，问题只在prompt 布局。

---

## 三、可落地的优化方向（未实施，留待决策）

按投入产出排序：

| # | 方案 | 预期收益 | 代价 |
|---|---|---|---|
| 1 | **快照移到 user message**，system 只留固定规程 | 把skill 规程+调度原则变成纯稳定前缀，命中率应从 0% 跳到 70%+ | 改 prompt 结构（base prompt 删快照段，`build_system_prompt` 不再format 快照）；需确认 function_calling 结构化输出不受影响 |
| 2 | skill 规程挪到快照**之前** | 同上，但快照仍在 system；改动更小 | 同上 |
| 3 | 同一案件多轮调度复用前缀 | 覆盖「同一案件多次进入 orchestrator」场景 | 需snapshot 稳定性设计 |

**注意**：方案 1 是本次测量最有价值的产出——当前 claimflow 在享受 DeepSeek 缓存默认开启的红利，但因prompt 布局导致**实际命中率≈0%**，白付全价。这正是 T162 把缓存变可观测的价值：黑盒状态下永远发现不了。

---

## 四、附带数据（30 案 LLM 评测，real key）

- 六门全绿，一致率 **100%**
- 成本：234,066 tokens / **7,802 tokens·案⁻¹** / **4.4 次 LLM 调用·案⁻¹**
- 报告：`evals/reports/t162_cache_llm30.json`

端到端冒烟 `scripts/verify_adjudication`：**23 通过 / 0 失败**（主链路 auto_issued 金额 4640.00 精确、补件闭环、unknown转人工、工单列表）。

---

## 五、踩坑记录

1. **容器跑旧镜像**：`docker compose up -d` 后 `/metrics` 无缓存指标。`docker compose exec app grep -c LLM_CACHE_TOKENS /app/services/observability/metrics.py` 返回 0 确诊，须 `docker compose build app` 重建（构建含 BGE-M3 层，约 6 分钟）。
2. **Grafana 面板引用不存在指标**：初版面板 13 用了 `claimflow_llm_cache_hit_tokens_bucket`（以为有直方图），实际只有 Counter，promql 返回 No data。改为 Counter 速率比值。
3. **面板语义重复**：面板 15（输入 Token 构成）与面板 12（缓存 Token 量堆叠）信息重叠，已删。

---

## 六、T164/T165 修复结果（2026-10-09 追加）

### 分层修复（D076）

| 层 | 任务 | 内容 |
|---|---|---|
| 观测 | T164 | 缓存指标加 stage 维度 + 四个裸调调用点接入（路由/审查/叙述经 phase_ainvoke，责任认定 create_agent 子图经回调 handler） |
| 布局 | T165 | 三 prompt 模板删动态占位段，三节点改 SystemMessage(全静态) + HumanMessage(动态数据) |

### 实测对比（真实 API，deepseek-flash）

| 调用点 | 改造前 | 改造后 | 性质 |
|---|---|---|---|
| **orchestrator 路由** | 0%（未被观测） | **86.8%**（三轮不同快照稳定） | 布局缺陷，已修 |
| **liability_judge 责任认定** | 0%（未被观测） | **73.0%**（4352 hit / 1608 miss） | 静态 system，天然高命中 |
| ocr 材料提取 | 0% | 0% | prompt 主体即唯一文档，**天然 miss，非缺陷** |

路由三轮实测：86.7% → 86.8% → 86.8%（快照各异而命中率恒定 = system 前缀逐字一致）

### 验收

- 528 passed + ruff 零退化
- 确定性主门 153 案：六门全绿、一致率 100%
- **注入对抗确定性 8/8 + LLM 档 8/8 全绿**——`<<<DATA>>>` 定界随数据移入 user message 后，D131注入防线未丢
- 30 案 LLM 评测：六门全绿、一致率 100%；tokens/案 7802 → 7659（prompt 变短），真实收益在命中部分计费折扣 1/10

### 过程中解决的技术障碍

1. **with_structured_output 返回纯 Pydantic 对象**——无 `usage_metadata` / `response_metadata`，usage 只能从 `on_llm_end` 的 `llm_output` 取。observed_ainvoke 结构化分支自动挂 LlmUsageCallbackHandler；判据用"是否自带模型身份属性"而非 `isinstance(BaseChatModel)`（测试替身同为非 BaseChatModel 但直返 AIMessage）
2. **langgraph 回调传播验证**——假模型探针因tool_call 无限循环失败，改真实模型 `invoke_worker` 验证，确认 `config.callbacks` 传播到子图内 chat model
3. **`_model_name` 递归探测**——RunnableSequence 需沿 `.first` 内层取真实模型名（实测取到 `deepseek-flash`）
