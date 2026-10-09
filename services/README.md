# services/ —— 服务层

领域服务与基础设施封装。节点层（`nodes/`）信任这里传参正确，错误处理只在
系统边界（外部 API / 用户输入）。

## 模块清单

### 案件域

| 模块 | 职责 |
|------|------|
| `case_service.py` | 案件领域服务：幂等建档 / 案号（`case_id_counters` 原子自增，T155）/ 决定书视图 / 工单投影 / resume 载荷单源 |
| `case_jobs.py` | 交付队列**生产半区**：JobAction / enqueue outbox / envelope / latest / deliver + facade re-export（保持既有 import 路径零改动，T153） |
| `case_job_worker.py` | 交付队列**消费半区**：CAS 租约认领（多实例安全，T155）+ 执行/ 重试 / 死信 / 派发器 + JobLoop（T153） |
| `case_resume_guard.py` | checkpoint 版本门卫：版本不匹配删旧重跑（T141/T155） |
| `case_store.py` | 案件 / 审计事件（append-only）落库 |
| `amounts.py` | 金额理算规格公式（Decimal，精确到分） |
| `decision_doc.py` | 决定书渲染 + 金额三方断言 + 红线检查 + 修订闭环 |
| `worker_agent.py` | Worker 子图装配：create_agent + 官方容错中间件栈；内部 LLM 调用经 `LlmUsageCallbackHandler` 回调观测（T164——langgraph 会把 `config.callbacks` 传播到节点内 chat model） |

### 支撑域

| 模块 | 职责 |
|------|------|
| `skills.py` | skill 作业规程装载（`<line>.md` → `_shared.md` → base 三级回退）+ `build_system_prompt`（**T165：system 只留全静态内容，动态数据一律走 user message**——DeepSeek 前缀缓存要求同一险种内 system 逐字一致） |
| `materials.py` | 材料两段式提取（图片/PDF/Word；文本 → 扫描件 vision；Mock 兜底） |
| `support/` | 在线客服域：store 状态机（ai→escalated→closed）+ Agent 装配（T132-T135） |
| `memory/` | long_term（Store 存储层）/ short_term / case_memory（申请人记忆治理） |
| `rag/` | embedder（BGE-M3）/ retriever / reranker / knowledge_graph / graph_retriever / qdrant_client / ingest |
| `llm/` | client（OpenAI 兼容 + token 埋点）/ prompts（**全量 prompt 单源**，T165 起静态指令段与动态数据段分离） |
| `cache.py` | 工具结果缓存（Redis，dev 内存降级） |
| `observability/` | metrics（含 Prompt 缓存指标三来源提取 + stage 维度，T162/T164）/ llm_metrics（`observed_ainvoke` +结构化输出回调分支 + `LlmUsageCallbackHandler`）/ token_tracker（`track_phase` / `phase_ainvoke` / `track_case`）/ tracing（OTel，采样率 0.2） |
| `db/` | models（11 张表）/ session（双后端引擎 + swap 公开接口） |

## 约定

- 单一职责，文件超 300 行考虑拆分（T153 口径：纯模型/数据定义文件豁免）；
- 对外系统全部 Mock（保单 / 病历 / 黑名单），Mock 结构参考真实理赔场景；
- LLM 交互全部经 `llm/client.py`（供应商可配置切换），不直连 SDK；
- **Prompt 布局约定（T165）**：DeepSeek 硬盘缓存按前缀命中，动态数据（案件快照 /
  材料提取结果 / 理算事实）一律放 user message 并用 `<<<DATA…>>>DATA` 定界，
  system 只保留逐字稳定的规程与原则——否则前缀分叉点在唯一数据开头，命中率归零。

架构见 [`../docs/architecture.md`](../docs/architecture.md)。
