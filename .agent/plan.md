# 技术方案 (Plan)

> Phase 2 产出。基于 spec.md 设计架构，用户确认后进入 Phase 3。
> 状态标记：✅ 已确认 | 🔄 待确认 | ❌ 需修改

**状态**：✅ 已确认（2026-08-24）

---

## 1. 技术选型

| 层面 | 选择 | 理由 |
|------|------|------|
| 语言 | Python 3.12 | 全量类型注解，AGENTS.md 约定 |
| Agent 框架 | LangGraph ≥0.2（实锁 1.2.11） | 状态机 + Checkpoint（AsyncPostgresSaver），条件边保证合规节点必经（ADR-001） |
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

**LangGraph checkpoint 表**：由 PostgresSaver / AsyncPostgresSaver 自动建表管理，不手工建模（D006/D009：类名无 "QL"，实际接入 AsyncPostgresSaver）。

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
| A07 | POST | /api/v1/conversations/{id}/materials（T049 起支持图片/PDF/Word，兼容别名 /images） | 上传材料提取结构化字段 | multipart/form-data: file | 200 {patient_name, diagnosis, amount, date, source: vision \| text_model \| mock_fallback, filename, file_type: image \| pdf \| docx}；不支持类型/.doc 旧格式/空文件/超限 422（D024） |

## 5. 第三方依赖

> 版本以 `uv add` 实际锁定为准。**2026-09-01 校验修正（D022）**：原表存在两处笔误——包名
> `langgraph-checkpoint-postgresql` 实为 `langgraph-checkpoint-postgres`（D009 已确认），
> Checkpoint 类名 `PostgreSQLSaver` 实为 `PostgresSaver` / `AsyncPostgresSaver`；
> 下表已按 uv.lock 实锁版本更新。

| 包名 | 实锁版本（uv.lock） | 用途 |
|------|------|------|
| fastapi | 0.141.1 | Web 框架 |
| uvicorn[standard] | 0.52.4 | ASGI 服务器 |
| langgraph | 1.2.11 | Agent 状态机 |
| langgraph-checkpoint-postgres | 3.1.2 | PostgreSQL Checkpoint（AsyncPostgresSaver） |
| langchain-core | 1.6.1 | 消息/工具抽象 |
| langchain-openai | 1.6.0 | DeepSeek（OpenAI 兼容）调用 |
| langchain | 1.3.18（2026-09-01 已装，D022） | `create_agent` 官方 agent 标准 |
| sqlalchemy[asyncio] | 2.0.52 | ORM |
| asyncpg / aiosqlite | 最新 | PostgreSQL / 开发降级驱动 |
| alembic | 1.19.1 | 迁移 |
| qdrant-client | 1.19.0 | 向量库（含 local mode） |
| sentence-transformers | 6.0.0 | 本地 BGE-M3 |
| redis | 8.1.0 | 缓存 |
| pydantic / pydantic-settings | 2.13.4 | 模型与配置 |
| structlog | 26.1.0 | 结构化日志 |
| httpx | 0.28.1 | HTTP 客户端（Mock/LLM） |
| gradio | 6.25.0 | 演示界面 |
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

---

## 7. Phase 6：三界面设计优化方案（2026-09-02，D030）

> 范围：用户聊天界面（ui/app.py）、坐席工作台（workbench/ Next.js）、评测台（ui/eval_app.py）。
> **硬约束：纯表现层重构——不改 API 契约、不改业务逻辑、不改 Gradio 回调元数（T053 教训）。**

### 7.1 设计语言（Apple Design 移植到 Web）

| 维度 | 方案 | 出处原则 |
|------|------|---------|
| 排版 | 系统字体栈（-apple-system/system-ui/PingFang/微软雅黑）；层级 = 字重+字号+行距组合；大标题负 tracking（-0.02em）、正文 0、小字正 tracking | §15 光学尺寸 |
| 色彩 | Apple 系统调色板：蓝 #007AFF / 绿 #34C759 / 橙 #FF9500 / 红 #FF3B30；背景 #F5F5F7、一级文字 #1D1D1F、次级 #86868B | §16 Craft |
| 材质 | 浮层 chrome 半透明化（backdrop-filter: blur+saturate），内容从其下滚过；hairline 分隔线替代粗边框；大面更重材质（更强 blur+更深阴影） | §12 材质与深度 |
| 圆角 | 连续圆角近似：卡片 14-18px、气泡 18px、控件 10px | — |
| 动效 | 按压即时反馈 `:active { transform: scale(0.97); transition: 100ms }`；统一 Apple 标准缓动 cubic-bezier(0.32, 0.72, 0, 1)；`prefers-reduced-motion` 降级为短淡入 | §1 Response / §14 无障碍 |
| 反馈 | 四态分级（status/completion/warning/error）：状态 pill 色彩语义化、行内错误、进度实时可见 | §16 反馈四类 |
| 简化 | 信息层级优先：KPI 大数字 + 权重分级；标签直接具体（"运行记录"优于"历史"）；高级选项退一层 | §6 Simplicity |

### 7.2 落地架构

- **ui/theme.py（新）**：两个 Gradio 应用的单一设计源——`build_theme()`（gr.themes.Base 令牌定制）+ `APP_CSS`（共享 CSS：材质/按压/气泡/状态 pill/进度条）。改设计只改一处。
- **workbench/app/globals.css**：Tailwind v4 `@theme` 设计变量 + 通用工具类（.card/.btn/.pill），与 Python 侧令牌同名同值，跨栈一致。
- 三界面各自消费令牌，业务回调逻辑零改动；评测台 poll 仍返回 8 输出（tests/ui 锁定）。

### 7.3 各界面改造点

**用户聊天界面（ui/app.py）**：半透明吸顶头部（品牌 + 状态点）；气泡重排（用户右/助手左、18px 圆角、hairline ring）；示例问题改 chips 横排；工具轨迹脚注改折叠卡片样式；输入区浮起（材质底 + 按压反馈按钮）。

**评测台（ui/eval_app.py）**：报告摘要改 KPI 大数字卡（完成率/工具准确率/合规率/耗时）；ASCII 进度条改 HTML 渐变进度条 + 状态 pill；运行日志等宽暗色块；趋势/报告区分组卡片化。

**坐席工作台（workbench/）**：全站 sticky 半透明导航（毛玻璃）；工单表 hover/按压反馈、状态 pill 精化；详情页风险分大数字 + verdict 印章；时间线气泡与审计展开动画（grid-rows 过渡）；ResolveForm 主按钮按压反馈 + focus ring；全站 reduced-motion/reduced-transparency 媒体查询。

### 7.4 验证策略

- ui/theme.py 单测（令牌非空、CSS 含关键选择器/媒体查询）
- 既有 tests/ui/test_eval_app.py 4 用例保持绿（元数锁定）
- 三界面真实启动 + 浏览器截图目检（7860/7861/3000）
- workbench `npm run build` 通过；全量 ruff + pytest 绿

---

## 8. Phase 6.5：Gradio 双界面视觉深化（2026-09-02，D031）

用户反馈：Phase 6 后仅坐席工作台（Next.js，全 CSS 自由度）达到预期；演示界面与评测台需按 impeccable 方法论进一步深化。

### 8.1 目标与约束

- 目标：两 Gradio 界面达到工作台水准的「深度感 / 生命感 / 细节感」
- 约束：纯表现层；Gradio 回调元数不变（poll 8 输出锁定）；TOKENS 核心值不动（双栈同源契约不破坏，新增 CSS 变量仅为派生令牌）

### 8.2 深化点（impeccable 方法论）

- 共享层（T059）：分层阴影（--cf-shadow-1/2/3）替代单层；卡片微渐变表面；页面顶部极淡蓝色 radial wash；cf-rise/cf-fade 入场动效（ease-out-quint，交错延迟）；区块标题体系（cf-kicker 大写小字 + cf-h2）；::selection 蓝色选区、自定义滚动条、隐藏 Gradio footer、placeholder 对比度、表格行 hover、输入焦点光环、primary 按钮 hover 升起
- 演示界面（T060）：头部品牌 logo 块（渐变方块）+ 状态 pill 化；气泡/输入区/chips 精修（hover 升起）；交错入场
- 评测台（T061）：趋势/历史报告改 gr.Tabs 分区；状态区卡化；KPI hover 升起；分类明细表加 mini 进度条（按通过率语义配色）；区块标题体系统一
- 验证（T062）：ruff + pytest 全绿；headless Chrome 截图目检；README 界面章节更新

### 8.3 任务链

T059（共享层）→ T060（演示界面）/ T061（评测台）→ T062（验证与文档）
