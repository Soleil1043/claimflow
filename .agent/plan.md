# 技术方案 (Plan)

> Phase 2 产出。基于 spec.md 设计架构，用户确认后进入 Phase 3。
> 状态标记：✅ 已确认 | 🔄 待确认 | ❌ 需修改

**状态**：✅ 已确认（2026-08-24）

---

## 1. 技术选型

| 层面 | 选择 | 理由 |
|------|------|------|
| 语言 | Python 3.12 | 全量类型注解，AGENTS.md 约定 |
| Agent 框架 | LangGraph ≥0.2 | 状态机 + Checkpoint（PostgreSQLSaver），条件边保证合规节点必经（ADR-001） |
| Web 框架 | FastAPI | async、类型安全、与 Pydantic 深度集成 |
| 关系数据库 | PostgreSQL 16 + SQLAlchemy 2.0 async | Checkpoint 原生支持；JSONB 存工具调用轨迹 |
| 向量数据库 | Qdrant ≥1.12 | 单容器轻量；开发期 local mode 零容器（D001 / ADR-004） |
| 缓存 | Redis 7 | 会话缓存、工具结果缓存 |
| LLM | 混合策略（D008）：主链路 `deepseek-v4-flash`；图片 OCR 专职 `deepseek-v4-flash-vision-exp`（失败降级 Mock） | 主链路用正式版稳定；vision-exp 真实 OCR 成亮点；两者价格相同、均支持 function calling + JSON 输出 |
| Embedding | BGE-M3（本地 sentence-transformers，1024 维） | 无外部依赖，文档量小 CPU 可跑（D003） |
| 演示界面 | Gradio ≥5.0 | ChatInterface 开箱即用、支持文件上传（D004） |
| 包管理 | uv | 快、锁文件可靠 |
| 测试 | pytest + pytest-asyncio | async 测试支持 |
| 日志 | structlog | 结构化日志约定 |
| 部署 | Docker Compose + GitHub Actions CI | 本地 Docker 异常，容器化验证走 CI（D005 profile 降级） |

## 2. 目录结构

> 与 AGENTS.md 第 5 节保持一致，补充 `data/`、`ui/`、`scripts/`。

```
claim-agent/
├── .agent/                    # AI 工具状态（只追加）
├── app/
│   ├── api/
│   │   ├── v1/
│   │   │   ├── conversations.py   # 会话/消息/文件上传路由
│   │   │   └── health.py          # 健康检查
│   │   └── dependencies.py        # 依赖注入（DB session、工具注册中心）
│   ├── core/
│   │   ├── config.py              # pydantic-settings，APP_PROFILE 开关
│   │   ├── logging.py             # structlog 配置
│   │   └── exceptions.py
│   └── main.py
├── agents/                        # 4 个 Agent 定义（prompt + 工具集 + 输出约束）
├── nodes/                         # LangGraph 节点（intent/planner/step_executor/compliance/generator/rag）
├── workflows/main_graph.py        # 主图组装与编译
├── state.py                       # AgentState
├── tools/
│   ├── base.py / registry.py / executor.py
│   ├── claim/                     # policy_query / calculator / claim_rule_rag / claim_status_query
│   ├── medical/                   # record_query / diagnosis_matcher / ocr_extract
│   └── compliance/                # rule_check / sensitive_filter / risk_scoring
├── services/
│   ├── llm/                       # client.py（DeepSeek 封装，双模型：主链路 + vision）/ prompts.py
│   ├── rag/                       # embedder.py / retriever.py / ingest.py
│   ├── memory/                    # short_term.py / working.py（long_term 后置）
│   └── db/                        # models.py / session.py（profile 降级）
├── schemas/                       # api.py / agent.py / tools.py
├── ui/app.py                      # Gradio 演示界面（D004）
├── data/
│   ├── mock/                      # 保单/就诊记录/理赔申请 Mock 数据（JSON，入库种子）
│   └── kb_docs/                   # RAG 知识库 markdown 文档（10-20 篇）
├── scripts/seed.py                # Mock 数据入库 + RAG 文档向量化入库
├── tests/                         # tools/ agents/ workflows/ api/
├── alembic/                       # 迁移
├── .github/workflows/ci.yml       # lint + test + compose 配置验证
├── Dockerfile / docker-compose.yml / .env.example
├── pyproject.toml / uv.lock
└── AGENTS.md / README.md / docs/
```

## 3. 数据模型

### conversations（会话）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK, default uuid4 | 会话 ID（即 LangGraph thread_id） |
| user_id | String(64) | not null, index | 演示期固定 demo 用户 |
| status | String(16) | not null, default 'active' | active / closed / transferred（转人工） |
| created_at | DateTime | not null, default now | |
| updated_at | DateTime | nullable | |

### messages（消息，业务审计层，D006）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BigInteger | PK, auto_increment | |
| conversation_id | UUID | FK → conversations.id, index | |
| role | String(16) | not null | user / assistant |
| content | Text | not null | 消息正文 |
| intent | String(32) | nullable | 意图分类结果（assistant 消息） |
| tool_trace | JSONB | nullable | 本轮工具调用明细 [{tool, input, output, duration_ms}] |
| agent_steps | JSONB | nullable | 多 Agent 执行计划与各步结果 |
| compliance_status | String(16) | nullable | PASS / MODIFIED / REJECTED |
| created_at | DateTime | not null, default now | |

### policies（Mock 保单）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BigInteger | PK | |
| policy_no | String(32) | unique, not null | 如 POL-2025-0001 |
| holder_name | String(64) | not null | 投保人 |
| holder_id_card | String(18) | not null, index | 身份证号 |
| product_name | String(128) | not null | 产品名 |
| product_type | String(32) | not null | 医疗险/重疾险/意外险 |
| coverage_amount | Numeric(12,2) | not null | 保额 |
| deductible | Numeric(12,2) | not null | 免赔额 |
| payout_ratio | Numeric(5,4) | not null | 赔付比例 |
| effective_date / expiry_date | Date | not null | 生效/到期 |
| status | String(16) | not null | active / expired / surrendered |

### medical_records（Mock 就诊记录）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BigInteger | PK | |
| patient_id_card | String(18) | not null, index | 关联投保人 |
| hospital / department | String(64) | not null | |
| diagnosis_desc | String(256) | not null | 诊断描述 |
| icd10_code | String(16) | not null | 如 K35（急性阑尾炎） |
| visit_date | Date | not null | |
| treatment | String(64) | not null | 门诊/住院手术等 |
| total_amount | Numeric(12,2) | not null | |

### claim_records（Mock 理赔申请）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BigInteger | PK | |
| claim_no | String(32) | unique | |
| policy_no | String(32) | not null, index | 逻辑关联 policies |
| status | String(16) | not null | submitted/reviewing/approved/rejected/paid |
| applied_amount / approved_amount | Numeric(12,2) | nullable | |
| submitted_at / updated_at | DateTime | not null | |

### kb_documents（RAG 文档元数据；向量与 chunk 存 Qdrant）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BigInteger | PK | |
| title | String(128) | not null | |
| source_file | String(256) | unique | data/kb_docs/ 相对路径 |
| category | String(32) | not null | 条款/理赔规则/免责说明/常见问题 |
| chunk_count | Integer | not null | |
| embedded_at | DateTime | not null | |

**Qdrant Collection**：`claim_rules`，向量 1024 维（BGE-M3），payload：`doc_id / title / category / chunk_index / text`。

**LangGraph checkpoint 表**：由 PostgreSQLSaver 自动建表管理，不手工建模（D006）。

### 表关系

- conversations.id → messages.conversation_id（一对多）
- policies.policy_no → claim_records.policy_no（一对多，逻辑外键）
- policies.holder_id_card → medical_records.patient_id_card（一对多，逻辑外键）

## 4. API 设计

| 编号 | Method | Path | 描述 | 请求体 | 响应 |
|------|--------|------|------|--------|------|
| A01 | GET | /health | 健康检查 | - | 200 {status, dependencies: {postgres, qdrant, redis, llm}} |
| A02 | POST | /api/v1/conversations | 创建会话 | {user_id} | 201 {conversation_id, created_at} |
| A03 | GET | /api/v1/conversations | 会话列表 | ?limit=20&offset=0 | 200 [{id, status, created_at}] |
| A04 | GET | /api/v1/conversations/{id} | 会话详情 | - | 200 {会话 + 最近消息摘要}；不存在返回 404 |
| A05 | GET | /api/v1/conversations/{id}/messages | 消息历史 | ?limit=50 | 200 [{role, content, intent, tool_trace, compliance_status, created_at}] |
| A06 | POST | /api/v1/conversations/{id}/messages | 发送消息（核心接口） | {content: str} | 200 {answer, intent, used_tools[], agent_steps[], compliance_status, need_human_intervention, intervention_reason?} |
| A07 | POST | /api/v1/conversations/{id}/files | 上传图片材料（vision-exp 真实 OCR + Mock 兜底） | multipart/form-data: file | 201 {file_id, ocr_result: {name, diagnosis, amount, date}, source: vision \| mock_fallback}；非图片 422 |

## 5. 第三方依赖

> 版本以 `uv add` 实际锁定为准，下表为最低版本约束。

| 包名 | 版本 | 用途 |
|------|------|------|
| fastapi | ≥0.115 | Web 框架 |
| uvicorn[standard] | ≥0.30 | ASGI 服务器 |
| langgraph | ≥0.2 | Agent 状态机 |
| langgraph-checkpoint-postgresql | ≥2.0 | PostgreSQL Checkpoint |
| langchain-core | ≥0.3 | 消息/工具抽象 |
| langchain-openai | ≥0.2 | DeepSeek（OpenAI 兼容）调用 |
| sqlalchemy[asyncio] | ≥2.0 | ORM |
| asyncpg / aiosqlite | 最新 | PostgreSQL / 开发降级驱动 |
| alembic | ≥1.13 | 迁移 |
| qdrant-client | ≥1.12 | 向量库（含 local mode） |
| sentence-transformers | ≥3.0 | 本地 BGE-M3 |
| redis | ≥5.0 | 缓存 |
| pydantic / pydantic-settings | ≥2.9 | 模型与配置 |
| structlog | ≥24.0 | 结构化日志 |
| httpx | ≥0.27 | HTTP 客户端（Mock/LLM） |
| gradio | ≥5.0 | 演示界面 |
| pytest / pytest-asyncio / pytest-cov | 最新 | 测试 |

## 6. 关键技术决策

> 详见 `.agent/decisions.md` D001-D006

- **D001** 向量库用 Qdrant 而非 Milvus：规模错配 + local mode 解除本地 Docker 依赖（同步更新 architecture.md ADR-003/004）
- **D002** LLM 用 DeepSeek：OpenAI 兼容接口，配置切换供应商
- **D003** Embedding 本地跑 BGE-M3：去外部依赖，文档量小 CPU 可承受
- **D004** 演示界面用 Gradio：ChatInterface + 文件上传开箱即用
- **D005** `APP_PROFILE=dev|prod`：dev 降级（Qdrant local mode / SQLite+MemorySaver / 内存缓存），prod 全量真实依赖，交付架构不降级
- **D006** messages 表与 checkpoint 并存：前者服务 API 展示与审计，后者服务状态机恢复
- **D007** LLM 模型确定 `deepseek-v4-flash`（旧别名 deepseek-chat 已退役，调用报错）
- **D008** 混合模型策略：主链路 flash 正式版 + OCR 专职 vision-exp + Mock 兜底，与架构 6.3"分级模型"自洽
