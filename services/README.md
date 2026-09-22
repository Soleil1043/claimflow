# services/ —— 服务层

领域服务与基础设施封装。节点层（`nodes/`）信任这里传参正确，错误处理只在
系统边界（外部 API / 用户输入）。

## 模块清单

### 案件域

| 模块 | 职责 |
|------|------|
| `case_service.py` | 案件领域服务：幂等建档 / 案号 / 决定书视图 / 工单投影 / resume 载荷单源 |
| `case_jobs.py` | 交付队列：CaseJob 表 + 常驻单消费者 + `deliver_case_job` 生命周期收口 + checkpoint 版本门卫（T141） |
| `case_store.py` | 案件 / 审计事件（append-only）落库 |
| `amounts.py` | 金额理算规格公式（Decimal，精确到分） |
| `decision_doc.py` | 决定书渲染 + 金额三方断言 + 红线检查 + 修订闭环 |
| `worker_agent.py` | Worker 子图装配：create_agent + 官方容错中间件栈 |

### 支撑域

| 模块 | 职责 |
|------|------|
| `skills.py` | skill 作业规程装载（`<line>.md` → `_shared.md` → base 三级回退） |
| `materials.py` | 材料两段式提取（图片/PDF/Word；文本 → 扫描件 vision；Mock 兜底） |
| `support/` | 在线客服域：store 状态机（ai→escalated→closed）+ Agent 装配（T132-T135） |
| `memory/` | long_term（Store 存储层）/ short_term / case_memory（申请人记忆治理） |
| `rag/` | embedder（BGE-M3）/ retriever / reranker / knowledge_graph / graph_retriever / qdrant_client / ingest |
| `llm/` | client（OpenAI 兼容 + token 埋点）/ prompts（全量 prompt 单源） |
| `cache.py` | 工具结果缓存（Redis，dev 内存降级） |
| `observability/` | metrics / llm_metrics / token_tracker / tracing（OTel） |
| `db/` | models（11 张表）/ session（双后端引擎 + swap 公开接口） |

## 约定

- 单一职责，文件超 300 行考虑拆分；
- 对外系统全部 Mock（保单 / 病历 / 黑名单），Mock 结构参考真实理赔场景；
- LLM 交互全部经 `llm/client.py`（供应商可配置切换），不直连 SDK。

架构见 [`../docs/architecture.md`](../docs/architecture.md)。
