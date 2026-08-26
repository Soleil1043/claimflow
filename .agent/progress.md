# 构建日志 (Progress)

> Phase 4 产出。每完成一个任务追加一条记录，只追加不删除。
> 格式：时间 | 任务编号 | 操作 | 涉及文件 | 验证方式 | 状态

---

## 日志条目

<!-- 每条记录格式：
### [T0XX] 任务名称 — YYYY-MM-DD HH:MM

**操作**：
- 创建/修改了哪些文件

**涉及文件**：
- `path/to/file.py` — [做了什么]

**验证方式**：
- [运行什么命令 / 看什么结果]

**状态**：✅ 通过验证 / ❌ 有问题（附描述）

**Git**：`abc1234` feat: T0XX 任务名称
-->

### [T001] 项目初始化与目录结构 — 2026-08-24

**操作**：
- 创建 pyproject.toml（Python 3.12 锁定、全量依赖、ruff/pytest 配置）
- 创建 .env.example（APP_PROFILE / LLM 双模型 / PG / Qdrant / Redis / Embedding 配置模板）与 .gitignore
- 按 AGENTS.md 第 5 节创建目录树（app/agents/nodes/tools/services/workflows/schemas/ui/scripts/data/evals/tests/grafana/prometheus/alembic），Python 包带 __init__.py，空目录带 .gitkeep
- `uv sync` 安装 146 个依赖（torch 2.13、langgraph、langgraph-checkpoint-postgres、qdrant-client、sentence-transformers 6.0、gradio 等）
- 核心包导入验证通过（含 AsyncPostgresSaver）
- git 初始化并首次 commit

**涉及文件**：
- `pyproject.toml` — 项目与依赖定义
- `.env.example` / `.gitignore` — 配置模板与忽略规则
- 目录树 + `__init__.py` × 18 + `.gitkeep` × 10
- `uv.lock` — 依赖锁文件

**验证方式**：
- `uv sync` exit 0，146 包安装成功
- `uv run python -c "import fastapi, langgraph, ..."` 输出 all imports OK

**状态**：✅ 通过验证

**问题与修正**：
- plan 中包名 `langgraph-checkpoint-postgresql` 有误，PyPI 实际为 `langgraph-checkpoint-postgres`（3.1.2），已修正（见 decisions.md D009）
- psycopg 纯 Python 实现在 Windows 缺 libpq，补 `psycopg[binary,pool]`（见 D009）

### [T002] 配置与日志基础 — 2026-08-24

**操作**：
- app/core/config.py：pydantic-settings 全量配置（APP_PROFILE / LLM 双模型 / PG / Qdrant / Redis / Embedding），含 database_url / checkpoint_conn_string 按 profile 切换的派生属性
- app/core/logging.py：structlog + stdlib ProcessorFormatter 集成，prod 输出紧凑 JSON、dev 输出彩色控制台，收敛第三方噪音日志
- app/core/exceptions.py：异常体系基类（ToolExecutionError / LLMError / ComplianceRejectedError 为后续任务预留）
- tests/core/：test_config.py（4 用例）+ test_logging.py（3 用例）

**涉及文件**：
- `app/core/config.py`、`app/core/logging.py`、`app/core/exceptions.py`
- `tests/core/test_config.py`、`tests/core/test_logging.py`

**验证方式**：
- `uv run pytest tests/core -v` → 7 passed
- `uv run ruff check app tests` → All checks passed

**状态**：✅ 通过验证

**问题与修正**：
- 测试初版两处错误：`logging.StreamHandler()` 默认写 stderr（测试误读 stdout）；`structlog.stdlib.get_logger` 返回惰性代理，`bind()` 后才是 BoundLogger 实例。均已修正。

### [T003] 数据库模型与会话管理 — 2026-08-24

**操作**：
- services/db/models.py：6 张表 ORM（SQLAlchemy 2.0 声明式，Mapped/mapped_column），JSONB→JSON variant 兼容 SQLite，BigInteger→Integer variant 解决 SQLite rowid 自增
- services/db/session.py：异步引擎/会话工厂单例、get_session 依赖（自动提交/回滚）、init_db 建表、dispose_engine
- alembic：init -t async 生成骨架，env.py 接入 settings.database_url（profile 动态切换）+ Base.metadata + render_as_batch（SQLite 兼容）；autogenerate 生成初始迁移 e01f31c574ab
- tests/db/：test_models.py（4 用例）+ test_session.py（3 用例），内存 SQLite 隔离运行
- pyproject.toml：ruff exclude alembic（工具生成代码不 lint）；.gitignore 补 data/*.db

**涉及文件**：
- `services/db/models.py`、`services/db/session.py`
- `alembic.ini`、`alembic/env.py`、`alembic/script.py.mako`、`alembic/versions/e01f31c574ab_create_core_tables.py`
- `tests/db/test_models.py`、`tests/db/test_session.py`
- `app/core/config.py`（补 _url_for_log 脱敏方法）

**验证方式**：
- `uv run pytest tests -q` → 14 passed（含 T002 的 7 个）
- `uv run alembic upgrade head` → SQLite 中 6 张表全部创建；`downgrade base` + 再 `upgrade head` 往返成功
- `uv run ruff check app tests services` → All checks passed
- prod 连接 PostgreSQL：本地 Docker 不可用，连接串构造已由 T002 测试覆盖，实际连接验证 deferred 到 T005 CI

**状态**：✅ 通过验证

**问题与修正**：
- SQLite 下 `BigInteger` 主键不走 rowid 自增 → `BigInteger().with_variant(Integer, "sqlite")`
- autogenerate 生成的迁移缺 `Text` import（JSONB variant 引用）→ 手动补导入
- scripts/seed.py 未在本任务实现（验收未涉及 seed 内容，Mock 数据入库与 T008 保单工具一并做，避免超前实现）

### [T004] FastAPI 骨架与健康检查 — 2026-08-24

**操作**：
- app/main.py：FastAPI 应用 + lifespan（启动 configure_logging / dev 建表，关停释放引擎）
- app/api/v1/health.py：/health 四依赖检查（postgres SELECT 1；qdrant dev=local mode 路径 / prod=get_collections 探活；redis dev=skipped / prod=PING；llm=配置完整性检查不真实调用），整体状态 ok/degraded/error 三态
- app/api/dependencies.py：get_db_session / get_app_settings 依赖注入
- schemas/api.py：HealthResponse / DependencyStatus
- tests/api/test_health.py：4 用例（全 ok / LLM 未配置 degraded / DB 故障 error / qdrant 路径不可写）

**涉及文件**：
- `app/main.py`、`app/api/v1/health.py`、`app/api/dependencies.py`、`schemas/api.py`
- `tests/api/test_health.py`

**验证方式**：
- `uv run uvicorn app.main:app --port 8000` 启动成功，结构化日志输出 app_started profile=dev
- `GET /health` 实测返回 200：status=ok，postgres ok / qdrant local mode / redis skipped / llm deepseek-v4-flash
- `uv run pytest tests -q` → 18 passed；`uv run ruff check` → All checks passed

**状态**：✅ 通过验证

**问题与修正**：
- health.py 初版两处把 `async with asyncio.timeout(...)` 误写成 `await asyncio.timeout(...)`（SyntaxError），已修正

### [T005] Docker Compose 与 CI 流水线 — 2026-08-24

**操作**：
- Dockerfile：python:3.12-slim + pip 装 uv（阿里云源）+ uv sync 分层缓存，启动命令含 alembic upgrade head
- docker-compose.yml：PostgreSQL/Qdrant/Redis/app 四服务，全部带 healthcheck，app 依赖三服务 healthy 后启动
- .github/workflows/ci.yml：lint + test + docker build + compose up 健康检查两 job
- .dockerignore：排除 .venv/data/.agent 等
- pyproject.toml：torch 切换 CPU 源并提升为直接依赖（镜像 3.86GB，避免 CUDA 膨胀 2-3GB）；默认 PyPI 源切换阿里云
- 本机 Docker daemon.json 配置 3 个国内镜像加速（宿主机配置，不在仓库内）

**涉及文件**：
- `Dockerfile`、`docker-compose.yml`、`.github/workflows/ci.yml`、`.dockerignore`
- `pyproject.toml`、`uv.lock`（128 包，全部阿里云源）

**验证方式**：
- `docker compose config -q` → 通过
- `docker compose build app` → 镜像构建成功（3.86GB）
- `docker compose up -d` → 四容器全部 Up，postgres/qdrant/redis healthy
- `GET http://localhost:8000/health` → 200，`status=ok, profile=prod`，postgres/qdrant/redis/llm 全部 ok（真实依赖，无降级）
- `psql \dt` → 6 张业务表 + alembic_version 已在 PostgreSQL 创建（容器启动迁移成功）
- CI 全绿验证：待推送 GitHub 后确认（本地已验证 lint + test）

**状态**：✅ 通过验证

**问题与修正（详见 decisions.md D010/D011）**：
- torch 传递依赖不应用 uv source → 提升为直接依赖
- 容器内 files.pythonhosted.org 仅 ~50KB/s → 默认源切阿里云，lock 全量重写
- ghcr.io / Docker Hub 直连超时 → pip 装_uv + daemon.json 镜像加速
- daemon.json UTF-8 BOM 导致 Docker 引擎崩溃 3 次 → 无 BOM 重写
- .dockerignore 排除 README.md 但 hatchling 构建需要 → 移出排除列表
- postgres 拉取触发 Docker Hub 回源致 WSL2 VM 静默崩溃 → docker logout + 镜像全部本地预热后规避

### [T006] LLM 客户端封装 — 2026-08-24

**操作**：
- services/llm/client.py：get_chat_model（主链路 deepseek-v4-flash）/ get_vision_model（vision-exp 专职 OCR）双模型单例，base_url/api_key/model 全配置化，含 reset_model_cache 测试辅助
- services/llm/prompts.py：通用助手 prompt 骨架（Agent 专属 prompt 随 T013/T015 补充）
- tests/llm/test_client.py：7 用例（双模型配置读取 / 单例缓存 / base_url 指向 / 供应商切换模拟 / mock 网络层 invoke / bind_tools 工具调用协议）

**涉及文件**：
- `services/llm/client.py`、`services/llm/prompts.py`
- `tests/llm/test_client.py`

**验证方式**：
- `uv run pytest tests -q` → 25 passed（累计）；`uv run ruff check` → All checks passed
- 真实调用 deepseek-v4-flash 返回测试响应：合并到 T012 届时提供 API Key 验证

**状态**：✅ 通过验证

**问题与修正**：
- langchain-openai 1.6.0 的 ainvoke 实际走 `async_client.with_raw_response.create`（非 root_client.create，且 async_client 是 AsyncCompletions 代理而非完整客户端），mock 需 patch 该实例方法并返回带 .parse() 的 raw response 对象——初版两次 patch 错对象导致真实 401 请求
- 测试环境无 Key 时 ChatOpenAI 实例化即报错 → fixture 注入占位 Key（网络层全 mock，无真实调用）

### [T007] 工具层基础设施 — 2026-08-24

**操作**：
- schemas/tools.py：ToolInput / ToolOutput 基类（success / error_message / data 三段结构）
- tools/base.py：BaseTool 泛型抽象类——execute() 统一入参校验 + 结构化日志（耗时/成功），子类只实现 _run()；to_openai_tool() 生成 function calling 定义
- tools/registry.py：ToolRegistry 注册/发现/重名拒绝/批量导出 Openai 工具定义，get_default_registry 全局单例
- tools/executor.py：ToolExecutor——超时（默认 10s 可覆盖）、指数退避重试（base 0.5s，最多 2 次）、熔断器（closed/open/half-open：5 连续失败→open 30s→半开探测）、可选 fallback 降级（architecture.md 4.3 / 7.2 全机制落地）
- tests/tools/test_infrastructure.py：18 用例（校验/日志/schema 导出/注册发现/重名/超时/fallback/重试成功/重试耗尽/熔断打开/熔断拒绝/熔断 fallback/半开恢复/半开再熔断/计数清零/未注册工具）

**涉及文件**：
- `schemas/tools.py`、`tools/base.py`、`tools/registry.py`、`tools/executor.py`
- `tests/tools/test_infrastructure.py`

**验证方式**：
- `uv run pytest tests -q` → 43 passed（累计）；`uv run ruff check` → All checks passed

**状态**：✅ 通过验证

**问题与修正**：
- executor 初版 __init__ 未存 failure_threshold/breaker_cooldown 实例属性（_breaker 引用时报 AttributeError）→ 补齐属性赋值

### [T008] Mock 数据与保单查询工具 — 2026-08-24

**操作**：
- data/mock/policies.json：5 张真实感保单（active×3 / expired / surrendered；POL-2025-0001 张伟医疗险 100 万/免赔 1 万/80% 为 F05 计算器标准用例，POL-2026-0005 刚生效用于等待期场景演示）
- tools/claim/policy_query.py：PolicyQueryTool——按保单号/身份证查询，支持多保单命中返回列表，session_factory 可注入（测试友好），业务失败（未找到/缺标识）返回 success=False 不抛错
- tools/claim/__init__.py：import 即注册到默认注册中心
- scripts/seed.py：幂等入库脚本（upsert：存在则更新不存在则插入），--only 参数支持分数据集入库，medical/claim/OCR 数据留接口随 T016/T020 扩展
- tests/tools/claim/test_policy_query.py：7 用例（按号查询/未找到/身份证单命中/身份证多命中/缺标识校验/过期保单返回/schema 导出）

**涉及文件**：
- `data/mock/policies.json`、`tools/claim/policy_query.py`、`tools/claim/__init__.py`
- `scripts/seed.py`、`scripts/__init__.py`
- `tests/tools/claim/test_policy_query.py`

**验证方式**：
- `uv run python -m scripts.seed` → inserted=5；重复执行 → updated=5（幂等验证通过）
- `uv run pytest tests -q` → 50 passed（累计）；`uv run ruff check` → All checks passed

**状态**：✅ 通过验证

### [T009] 理赔计算器工具 — 2026-08-24

**操作**：
- tools/claim/calculator.py：ClaimCalculatorTool——标准绝对免赔算法 `可赔基数 = max(0, min(费用, 保额) - 免赔额)`，`预估赔付 = 基数 × 比例`；Decimal 全程计算 + ROUND_HALF_UP 到分；返回 calculation_detail 明细供 Agent 解释金额构成；入参 validator 宽松接受 str/float/int
- tools/claim/__init__.py：注册 claim_calculator
- tests/tools/claim/test_calculator.py：10 用例（标准用例 4640/费用超保额封顶 792000/费用低于免赔自担/免赔超保额无可赔/零免赔重疾/四舍五入精度/字符串入参/非法比例拒绝/负费用拒绝/schema 导出）

**涉及文件**：
- `tools/claim/calculator.py`、`tools/claim/__init__.py`
- `tests/tools/claim/test_calculator.py`

**验证方式**：
- `uv run pytest tests -q` → 60 passed（累计）；`uv run ruff check` → All checks passed

**状态**：✅ 通过验证

**问题与修正**：
- 初版公式写成 `min(费用, 保额-免赔额)` 语义错误（费用 8000 < 免赔 10000 时仍能算出赔付 6400，被测试当场抓住）→ 修正为标准绝对免赔算法 `min(费用, 保额) - 免赔额`，同步修正测试期望值（15800 用例：可赔基数 5800，赔付 4640）

### [T010] RAG 知识库与检索工具 — 2026-08-25

**操作**：
- data/kb_docs/：12 篇真实感知识库文档（条款要点×2、理赔规则手册、免责条款汇总、等待期详解、进度指南、FAQ×2、意外险规则、ICD-10 对照、医保目录说明、审核疑点标准）
- services/rag/embedder.py：BGE-M3 惰性单例加载（1024 维，normalize embeddings），embed_texts 批量 / embed_query 单条
- services/rag/qdrant_client.py：客户端工厂（dev=local mode 零容器 / prod=服务连接，@cache 单例）
- services/rag/ingest.py：markdown 按二级标题分块（超长段落细切，块首附文档主题上下文）→ 向量化 → Qdrant upsert + kb_documents 元数据表同步（幂等，--force 可重灌）
- services/rag/retriever.py：query_points 相似度检索 top-k，返回带分数的 RetrievedChunk
- tools/claim/claim_rule_rag.py：ClaimRuleRagTool（query/top_k 入参，无结果 success=False）
- scripts/verify_rag.py：F06 验收检索质量脚本

**涉及文件**：
- `data/kb_docs/*.md`（12 篇）、`services/rag/embedder.py`、`services/rag/qdrant_client.py`、`services/rag/ingest.py`、`services/rag/retriever.py`
- `tools/claim/claim_rule_rag.py`、`tools/claim/__init__.py`、`scripts/verify_rag.py`
- `tests/rag/test_retriever.py`（11 用例）

**验证方式**：
- `uv run python -m services.rag.ingest` → 12 文档 53 chunks 入库（Qdrant local mode + BGE-M3 真实模型）
- `uv run python -m scripts.verify_rag` → "阑尾炎手术有等待期吗" top-1 命中等待期规则详解·阑尾炎案例（score 0.758）；"理赔需要什么材料" top-1 命中材料清单（0.749）；"既往症能赔吗" top-1 命中既往症免责（0.713），均按相似度降序
- `uv run pytest tests -q` → 71 passed（累计）；`uv run ruff check` → All checks passed

**状态**：✅ 通过验证

**问题与修正**：
- hf-mirror.com 连接不稳定（transformers 5.x + hf-xet 下载器）→ 直连 huggingface.co（实测 1.9s 可达）
- C 盘初始仅剩 500MB 不够模型 2.2GB → 用户清理后恢复默认缓存路径（C:\Users\...\.cache\huggingface）
- 测试初版一处函数内 import 位于使用之后（UnboundLocalError）→ 移至函数开头

### [T011] 对话 API 与状态持久化 — 2026-08-25

**操作**：
- schemas/api.py：A02-A05 请求/响应模型（ConversationCreate/List/Detail + MessageItem 含 tool_trace/agent_steps/compliance_status 审计字段）
- app/api/v1/conversations.py：A02 创建（201+UUID）/ A03 列表（倒序分页+message_count）/ A04 详情（最近 5 条摘要+404）/ A05 历史（正序+审计字段往返）
- services/memory/short_term.py：CheckpointManager——start/close 生命周期 + checkpointer 属性（dev=InMemorySaver，prod=AsyncPostgresSaver 含 setup() 建表；未初始化访问抛错防静默降级）；MAX_HISTORY_MESSAGES=20 滑窗常量
- app/main.py：注册 conversations 路由
- tests/api/test_conversations.py：13 用例（CRUD 全路径 + 分页 + 计数 + 404 + 审计字段 + CheckpointManager 生命周期/幂等/未初始化抛错）

**涉及文件**：
- `schemas/api.py`、`app/api/v1/conversations.py`、`app/main.py`
- `services/memory/short_term.py`
- `tests/api/test_conversations.py`

**验证方式**：
- `uv run pytest tests -q` → 82 passed（累计）；`uv run ruff check` → All checks passed
- uvicorn 冒烟实测：A02 create 201（UUID 返回）/ A03 list total=1 / A04 detail + 404 / A05 messages

**状态**：✅ 通过验证

**问题与修正**：
- AsyncPostgresSaver.from_conn_string 返回 async context manager 而非 saver 实例（初版工厂直接返回导致测试断言失败，暴露真实设计缺陷）→ 重构为 CheckpointManager 持有者模式（lifespan 管理 __aenter__/__aexit__，官方 setup() 建表约定）
- pydantic property 不能 monkeypatch（checkpoint_conn_string）→ 测试改 patch 底层 postgres 字段
- FastAPI Depends 默认参数触发 ruff B008 → noqa（FastAPI 惯用法）

### [T012] 单 Agent ReAct 核心流程 — 2026-08-25（Phase 1 里程碑）

**操作**：
- state.py：AgentState 全量字段定义（total=False 局部更新，messages 用 add_messages reducer 累积）
- nodes/generator.py：ReactAgentNode——LLM bind_tools（OpenAI dict 格式）→ tool_calls 经 ToolExecutor 执行 → ToolMessage 回填循环；should_continue 条件边（末尾 ToolMessage → 继续 / AIMessage → 结束）；MAX_TOOL_ROUNDS=8 防失控
- workflows/main_graph.py：Phase 1 简版图（START → react_agent ⇄ 循环 → END，checkpointer 注入）+ create_default_graph 工厂
- app/api/v1/conversations.py：A06 send_message——graph.ainvoke（thread_id=conversation_id）→ final_answer + 本轮 tool_trace 落审计表 → 返回 answer/used_tools
- app/main.py + dependencies.py：lifespan 组装（registry → checkpointer → graph 挂 app.state），get_app_graph 依赖
- tests/workflows/test_phase1_graph.py：6 用例（ScriptedLLM mock：ReAct 循环消息序列/多轮 checkpoint 历史/条件边三分支/A06 协议/404/422）

**涉及文件**：
- `state.py`、`nodes/generator.py`、`workflows/main_graph.py`
- `app/api/v1/conversations.py`、`app/api/dependencies.py`、`app/main.py`、`schemas/api.py`
- `tests/workflows/test_phase1_graph.py`、`.env`（本地真实 Key，不入 git）

**验证方式（真实 DeepSeek LLM 端到端）**：
- API Key 验证：deepseek-v4-flash 真实调用返回"收到"（T006 顺延验收补齐）
- F07 主验收："保单 POL-2025-0001 住院花了15800元能赔多少？" → LLM 自主调用 policy_query(policy_no=POL-2025-0001) → claim_calculator(medical_expense=15800, coverage=1000000, deductible=10000, ratio=0.8) → 回答含保单全信息 + 计算明细（可赔基数 5,800 → 赔付 **4,640 元**，与 T009 标准用例一致）
- F14 多轮：同会话追问"免赔额多少" → 正确引用第一轮上下文回答 10,000 元；历史 4 条消息审计落库
- `uv run pytest tests -q` → 88 passed（累计）；`uv run ruff check` → All checks passed

**状态**：✅ 通过验证

**问题与修正**：
- langgraph 当前版无 `get_callbacks` → 改用 langchain_core 的 `ensure_config()`
- bind_tools 传项目 BaseTool 实例报 "Unsupported function" → 必须传 OpenAI dict 格式（to_openai_tool()）
- used_tools 语义：checkpoint 恢复导致轨迹跨轮累积 → A06 每轮显式重置 tool_trace=[]（messages 累积、轨迹本轮）
- ASGITransport 不跑 lifespan → 测试直接给 app.state.graph 赋值（Depends 绑定的函数对象无法 monkeypatch 模块属性）
- SQLite 同秒 created_at 排序不稳定（偶发测试失败）→ 分页测试直插递增时间戳

### [T013] 意图识别节点 — 2026-08-25

**操作**：
- data/mock/intent_test_cases.json：20 条标注测试集（simple_faq×6 / single_domain×4 / multi_step×4 / chitchat×4 / other×2）
- services/llm/prompts.py：INTENT_CLASSIFICATION_PROMPT（五类定义 + 分类原则 + JSON 输出格式）
- nodes/intent.py：classify_intent（LLM 结构化输出 → JSON 解析容忍 markdown 包裹/前后缀 → 非法输出或异常走关键词规则兜底，节点永不抛错）；intent_node LangGraph 封装（T021 接入主图分流）
- schemas/agent.py：IntentResult / TaskStep / TaskPlan（T017 预留）
- tests/nodes/test_intent.py：11 用例（JSON 解析 4 / 关键词兜底 / LLM 成功 / 非法标签兜底 / 非 JSON 兜底 / 异常兜底 / 空输入 / 节点封装）
- scripts/verify_intent.py：F03 验收脚本

**涉及文件**：
- `nodes/intent.py`、`schemas/agent.py`、`services/llm/prompts.py`
- `data/mock/intent_test_cases.json`、`scripts/verify_intent.py`
- `tests/nodes/test_intent.py`

**验证方式**：
- `uv run python -m scripts.verify_intent` → 真实 LLM 准确率 **19/20 = 95%**（≥90% 验收线通过），0 次关键词兜底；唯一误分类为"申请理赔流程材料"（simple_faq/multi_step 边界案例，可接受）
- `uv run pytest tests -q` → 99 passed（累计）；`uv run ruff check` → All checks passed

**状态**：✅ 通过验证

### [T014] Gradio 演示界面 — 2026-08-25（Phase 1 完结）

**操作**：
- ui/app.py：Gradio Blocks 界面——BackendClient（httpx 异步，A02 惰性创建会话 + A06 发消息）、chat 回调（回答 + ⚙️ 工具轨迹脚注）、欢迎语、示例问题、新会话重置、后端不可达/HTTP 错误友好提示；API_BASE_URL 环境变量支持容器分离部署
- scripts/verify_ui.py：验收脚本（模拟界面回调，真实后端两轮对话）

**涉及文件**：
- `ui/app.py`、`scripts/verify_ui.py`

**验证方式**：
- 界面启动：http://127.0.0.1:7860 返回 HTTP 200
- `uv run python -m scripts.verify_ui`（真实后端）：第一轮"保单 POL-2025-0001 住院花了15800元能赔多少？" → 回答含 4,640 元 + policy_query/claim_calculator 工具轨迹；第二轮"免赔额多少" → 正确引用上下文 10,000 元 → 全链路验收通过
- `uv run pytest tests -q` → 99 passed；`uv run ruff check`（含 ui）→ All checks passed

**状态**：✅ 通过验证

**问题与修正**：
- _WELCOME 字符串内误用 ASCII 双引号截断字符串（SyntaxError）→ 改中文书名引号「」
- gradio 6.25 的 Chatbot 已移除 type / show_copy_button 参数（messages 格式为默认）→ 去除失效参数

### [T015] Agent 定义与 Prompt 体系 — 2026-08-25（Phase 2 开篇）

**操作**：
- agents/base.py：AgentDefinition 数据类（name/display_name/system_prompt/tool_names/output_schema/description + resolve_tools 注册中心解析过滤未注册工具）
- agents/orchestrator.py|claim.py|medical.py|compliance.py：4 个 Agent 定义（Orchestrator 无业务工具走结构化输出；Claim 4 工具；Medical 3 工具未实现+复用 RAG；Compliance 3 工具未实现）
- services/llm/prompts.py：4 个 Agent system prompt（职责/工作规范/JSON 输出格式；Compliance 含一票否决与五类违规标准 PROMISE/ABSOLUTE/MISLEAD/FRAUD_RISK/PRIVACY）
- schemas/agent_outputs.py：ClaimAgentOutput / MedicalAgentOutput / ComplianceAgentOutput（含 Violation 嵌套）/ OrchestratorPlan（含 PlanStep）
- agents/__init__.py：ALL_AGENTS 注册表 + get_agent
- tests/agents/test_definitions.py：14 用例（定义完整性/工具分配/prompt 关键约束/4 个 schema 合法非法校验/resolve_tools 过滤与全量解析）

**涉及文件**：
- `agents/`（base + 4 定义 + __init__）、`services/llm/prompts.py`
- `schemas/agent_outputs.py`、`tests/agents/test_definitions.py`

**验证方式**：
- `uv run pytest tests -q` → 113 passed（累计）；`uv run ruff check`（含 agents）→ All checks passed

**状态**：✅ 通过验证

**设计说明**：
- Agent 是静态描述（prompt+工具集+schema），执行逻辑在节点/图——与 AGENTS.md 6.2"Agent 不直接调工具"一致
- 跨任务工具依赖（record_query 等随 T016/T018 实现）用 resolve_tools 过滤策略：定义先行不阻断，工具就绪自动生效

### [T016] 医疗审核 Agent 工具 — 2026-08-25

**操作**：
- data/mock/medical_records.json：5 条就诊记录（与保单人物关联：张伟阑尾炎住院 15800 + 胃炎门诊、李娜高血压、王强支气管炎、刘洋肾结石住院）
- tools/medical/record_query.py：RecordQueryTool——按身份证查询就诊记录（倒序），session_factory 可注入
- tools/medical/diagnosis_matcher.py：DiagnosisMatcherTool——ICD-10 匹配（13 编码对照表 + 18 关键词映射，显式编码优先）+ 保障范围结论 + 等待期计算（就诊日 vs 保单生效日，30 天规则，正好覆盖 POL-2026-0005 演示场景）
- tools/medical/__init__.py：注册两工具（Medical Agent resolve_tools 自动生效）
- scripts/seed.py：medical_records 幂等入库（身份证+就诊日期+诊断组合判重）
- tests/tools/medical/test_medical_tools.py：10 用例（查询倒序/无记录/K35 主用例/显式编码/未知诊断/等待期内/已过/无日期跳过/2 个 schema）

**涉及文件**：
- `data/mock/medical_records.json`、`tools/medical/record_query.py`、`tools/medical/diagnosis_matcher.py`、`tools/medical/__init__.py`
- `scripts/seed.py`、`tests/tools/medical/test_medical_tools.py`

**验证方式**：
- `uv run python -m scripts.seed` → medical_records inserted=5；重复执行 updated=5（幂等）
- `uv run pytest tests -q` → 123 passed（累计）；`uv run ruff check` → All checks passed

**状态**：✅ 通过验证

**设计说明**：
- 材料缺失清单：ICD-10 对照表 + 就诊记录的 treatment 字段（住院手术需要材料）由 Medical Agent 的 LLM 在 T017 步骤执行时综合工具结果生成 missing_materials，工具层不重复实现清单逻辑

### [T017] 任务规划与步骤执行节点 — 2026-08-25

**操作**：
- services/llm/prompts.py：TASK_PLANNER_PROMPT（Agent 职责注入 + 规划原则 + 阑尾炎 few-shot 示例）
- nodes/planner.py：create_plan（LLM 结构化输出 → 非法 Agent 过滤 + step_index 重排 → 异常/空步骤走关键词规则兜底：金额+医疗→medical→claim 两步 / 仅金额→claim / 仅医疗→medical）；planner_node 节点封装（写 task_plan/current_step=0/shared_data={}）
- agents/runner.py：run_worker_agent 增加可选 tool_trace 参数（就地追加 {agent, tool, input, output}，F08 执行追溯）
- nodes/step_executor.py：StepExecutorNode——按 current_step 取步骤 → get_agent → run_worker_agent → 结果写 shared_data[agent_name]、状态回写 task_plan（done/failed）、agent_steps 档案（描述/状态/耗时/摘要）；未知 Agent / 执行异常降级为 failed 不阻断整体；has_next_step 条件边（next/done）
- state.py：新增 agent_steps 字段（执行步骤档案）；schemas/agent.py：TaskStep.step_index 默认 0
- tests/nodes/test_planner_executor.py：19 用例（兜底规则 4 / LLM 规划 6 / 节点封装 / 步骤执行 6 / 条件边）
- scripts/verify_planner.py：F08 真实 LLM 端到端验收脚本

**涉及文件**：
- `nodes/planner.py`、`nodes/step_executor.py`、`agents/runner.py`、`state.py`、`schemas/agent.py`
- `services/llm/prompts.py`、`scripts/verify_planner.py`、`tests/nodes/test_planner_executor.py`

**验证方式（真实 DeepSeek LLM 端到端）**：
- `uv run python -m scripts.verify_planner` → "我做了阑尾炎手术能赔多少"生成 2 步计划（medical→claim，无兜底）依次执行：medical 调 diagnosis_matcher + claim_rule_rag×2（K35 保障范围 + 等待期规则），claim 调 claim_rule_rag×2；两步结果均入 shared_data；agent_steps 记录 2 步（均 done，41.6s/14.8s）；tool_trace 5 次工具调用可追溯 → F08 验收通过
- `uv run pytest tests -q` → 142 passed（累计）；`uv run ruff check` → All checks passed

**状态**：✅ 通过验证

**问题与修正**：
- TaskStep.step_index 无默认值导致 LLM 输出步骤（不含 step_index）校验失败被整体降级为兜底计划 → schema 给默认值 0，由 create_plan 统一重排
- StepExecutor 传给 run_worker_agent 的 shared_data 为同一可变对象，步骤完成后回写会产生别名污染 → 传 dict(shared) 快照

### [T018] 合规审查节点与三态流转 — 2026-08-25

**操作**：
- tools/compliance/rule_check.py：五类违规正则检测（PROMISE/ABSOLUTE/MISLEAD/FRAUD_RISK/PRIVACY：身份证/手机号/银行卡，身份证与银行卡片段去重）+ 纯函数 check_text；证据片段脱敏展示
- tools/compliance/risk_scoring.py：加权评分（FRAUD_RISK 60 / PRIVACY 30 / MISLEAD 20 / PROMISE 15 / ABSOLUTE 10 + 组合欺诈信号，封顶 100）+ 等级 low/medium/high + 纯函数 score_risk
- nodes/compliance.py：review_answer（工具取证 → LLM 裁决 → 确定性兜底：FRAUD_RISK 或 risk≥80→REJECT，其他违规→MODIFY，无违规→PASS）；ComplianceNode（REJECT 替换安全话术 + need_human_intervention + intervention_reason）；revise_answer_node（LLM 重写 + 正则兜底替换）；compliance_route 三态条件边（MODIFY 闭环 compliance_rounds 上限 2）
- workflows/main_graph.py：react_agent 的 end 边改路由到 compliance（所有输出路径必经合规节点），compliance → pass/modify/reject 三态，revise_answer → compliance 复审闭环
- app/api/v1/conversations.py A06：返回 compliance_status/need_human_intervention/intervention_reason；审计落库 compliance_status；REJECT 时会话标记 transferred；每轮重置合规状态
- services/llm/prompts.py：COMPLIANCE_REVIEW_PROMPT（裁决模板）+ REVISE_ANSWER_PROMPT（修订模板）
- state.py：新增 compliance_result / compliance_rounds 字段
- tests/nodes/test_compliance.py：25 用例（工具正则 6 / 评分 4 / review LLM 与兜底 7 / 节点 2 / 修订 3 / 路由 1 / 图集成 MODIFY 闭环 + REJECT 拦截 2）；tests/workflows/test_phase1_graph.py fixture 适配（合规 LLM mock + 注册合规工具）
- scripts/verify_compliance.py：F10 真实 LLM 验收脚本（mini 图复刻 main_graph 合规接线）
- 设计决策 D012：合规节点走"工具取证 + LLM 裁决 + 确定性兜底"（非 run_worker_agent，保证 verdict 三态在 LLM 故障时不丢失）

**涉及文件**：
- `tools/compliance/rule_check.py`、`tools/compliance/risk_scoring.py`、`tools/compliance/__init__.py`
- `nodes/compliance.py`、`workflows/main_graph.py`、`state.py`
- `app/api/v1/conversations.py`、`services/llm/prompts.py`
- `tests/nodes/test_compliance.py`、`tests/workflows/test_phase1_graph.py`、`scripts/verify_compliance.py`、`.agent/decisions.md`（D012）

**验证方式（真实 DeepSeek LLM 端到端）**：
- `uv run python -m scripts.verify_compliance` → 场景 1"保证赔付 4,640 元"草稿被 MODIFY 拦截（检出 PROMISE + 具体修改建议"改为预估表述…最终以理赔审核结果为准"），修订闭环 2 轮后复审 PASS，修订后回答不含承诺话术；场景 2 欺诈草稿（代开发票+挂床）被 REJECT（risk_score=98），final_answer 替换为转人工安全话术，need_human_intervention=True，违规原文不返回 → F10 验收通过
- `uv run pytest tests -q` → 167 passed（累计）；`uv run ruff check` → All checks passed

**状态**：✅ 通过验证

**问题与修正**：
- ComplianceNode PASS 路径不写 final_answer（LangGraph 局部更新语义），初版测试误断言 KeyError → 修正断言为"不包含 final_answer 键"

### [T019] 敏感信息脱敏工具 — 2026-08-25

**操作**：
- tools/compliance/sensitive_filter.py：SensitiveFilterTool——身份证（18 位，前 4 后 4）→ 3301**********1234；银行卡（16-19 位，前 4 后 4）；手机号（11 位，前 3 后 4）→ 138****5678；纯函数 mask_sensitive / find_sensitive（检测明细含原文与脱敏值）；正则与 rule_check PRIVACY 检测共用（单一来源），替换顺序身份证→银行卡→手机号天然去重（星号段不再命中后续正则）
- tools/compliance/__init__.py：注册 sensitive_filter（Compliance Agent resolve_tools 自动生效）
- agents/compliance.py：docstring 更新（工具已就绪）
- tests/tools/compliance/test_sensitive_filter.py：24 用例（身份证 4 含 X 后缀/数字边界/15 位不命中；银行卡 3 含 16/19 位/超长不命中；手机号 3 含全号段/12 开头不命中/短号不命中；混合/去重/幂等/干净文本；find_sensitive 2；BaseTool 执行/注册/schema/空入参/独立注册中心 6；参数化单值 3）

**涉及文件**：
- `tools/compliance/sensitive_filter.py`、`tools/compliance/__init__.py`、`agents/compliance.py`
- `tests/tools/compliance/test_sensitive_filter.py`

**验证方式**：
- `uv run pytest tests -q` → 191 passed（累计）；`uv run ruff check` → All checks passed
- F11 验收演示：混合文本"张三 330106199001011234，手机 13812345678，卡号 6222020200112233445" → 一次全部脱敏为"3301**********1234 / 138****5678 / 6222***********3445"，且幂等（二次脱敏不变）

**状态**：✅ 通过验证

**问题与修正**：
- 初版测试 6 处期望值笔误（19 位银行卡中间应为 11 个星号；一处遗漏文本前缀；手机号拼接少一位成 10 位）——实现本身正确，修正测试期望

### [T020] OCR 图片上传 — 2026-08-25

**操作**：
- tools/medical/ocr_extract.py：OcrExtractTool——get_vision_model 多模态消息（text + image_url data URL）→ OCR_EXTRACT_PROMPT 提取姓名/诊断/金额/日期 → JSON 容错解析 + 金额宽松归一化（"15,800.00 元"→15800.0）；vision 异常/解析失败/金额不可归一化 → 读 data/mock/ocr_fallback.json 兜底（source 标记），接口不抛错
- app/api/v1/conversations.py A07：POST /{id}/images（multipart 上传）——MIME 白名单（png/jpeg/webp/bmp，content_type 缺失按扩展名推断），非图片 422、空文件 422、会话 404；OCR 结果落审计消息（user 上传行为 + assistant 识别摘要，tool_trace 记录）
- data/mock/ocr_fallback.json：预置兜底数据（张伟/急性阑尾炎/15800/2026-08-10，与 medical_records 场景一致）
- services/llm/prompts.py：OCR_EXTRACT_PROMPT（字段提取模板）
- schemas/api.py：OcrResultResponse（字段 + source + filename）
- ui/app.py：上传组件（gr.File image 类型 + 识别按钮）→ A07 → 识别结果以对话消息展示（来源标记 vision/mock_fallback）
- tools/medical/__init__.py 注册 ocr_extract（Medical Agent 自动生效）；agents/medical.py docstring 更新
- tests/tools/medical/test_ocr_extract.py：16 用例（纯函数 2 / 工具层 7：vision 成功/异常兜底/解析失败/金额坏值兜底/字符串金额归一化/空入参/schema/注册 / API 层 7：上传 200+字段+审计/非图片 422/扩展名推断/vision 故障 200 走 Mock/404/空文件）
- scripts/verify_ocr.py：F12 真实 vision 验收（PIL 生成诊断证明图片）

**涉及文件**：
- `tools/medical/ocr_extract.py`、`tools/medical/__init__.py`、`agents/medical.py`
- `app/api/v1/conversations.py`、`schemas/api.py`、`services/llm/prompts.py`
- `data/mock/ocr_fallback.json`、`ui/app.py`
- `tests/tools/medical/test_ocr_extract.py`、`scripts/verify_ocr.py`

**验证方式（真实 DeepSeek vision API 端到端）**：
- `uv run python -m scripts.verify_ocr` → PIL 生成诊断证明图（张伟/急性阑尾炎 K35/15800.00/2026-08-10）→ deepseek-v4-flash-vision-exp 真实识别四字段全部正确（姓名=张伟、诊断=急性阑尾炎、金额=15800.0、日期=2026-08-10，source=vision，7.2s）；模拟 vision API 故障 → 返回预置 Mock 数据（source=mock_fallback），接口不报错 → F12 验收通过
- `uv run pytest tests -q` → 207 passed（累计）；`uv run ruff check`（含 ui）→ All checks passed

**状态**：✅ 通过验证

**设计说明**：
- OCR 工具在 A07 内直接实例化执行（不经 ToolExecutor 循环），避免上传接口与对话图耦合；工具同时注册供 Medical Agent 的 LLM 工具调用路径（agent 侧传 base64）
- 金额不可归一化视为识别失败走兜底（而非返回 null），保证下游理赔计算拿到的金额要么可信要么明确是 Mock 数据

### [T021] 主图组装与端到端联调 — 2026-08-25（Phase 2 收官）

**操作**：
- workflows/main_graph.py：build_main_graph 完整图——START → intent → route_intent 三分流（multi_step→planner / simple_faq→rag / 其余→react_agent）；planner → step_executor 循环（has_next_step）→ synthesize；rag → synthesize；react_agent 工具循环（should_continue）→ compliance；synthesize → compliance；compliance 三态（pass/modify/reject）+ revise_answer 复审闭环；删除 phase1 简版图（完整图取代）
- nodes/generator.py：新增 synthesize_answer_node——汇总 shared_data（Agent 结论 / RAG 上下文）+ 消息历史生成最终回答（ANSWER_SYNTHESIS_PROMPT，历史截断 10 条 / 上下文截断 6000 字符）；LLM 失败确定性兜底拼接各数据源 summary
- nodes/rag.py：rag_node——读末尾用户问题 → search_kb top-4 → 写 shared_data.rag_context；检索故障/空结果不抛错（标记后 synthesize 兜底）
- services/llm/prompts.py：ANSWER_SYNTHESIS_PROMPT（背景数据 + 对话历史 → 最终回答，含合规约束）
- app/api/v1/conversations.py A06：每轮全量重置 11 个状态字段（checkpoint 只累积 messages）；返回完整结构 answer/intent/used_tools/agent_steps/compliance_status/need_human_intervention/intervention_reason；审计落库 intent + agent_steps
- app/main.py：lifespan 改挂 build_main_graph；注册全部三类工具（claim/compliance/medical）
- tests/workflows/test_full_graph.py：7 用例（route_intent 三分支 / multi_step 全链路 / synthesize 兜底 / RAG 路径 / 检索空结果 / 检索故障不致命 / F14 重启恢复——共享 checkpointer 重建图实例）；test_phase1_graph.py 与 test_compliance.py 适配完整图（intent mock）
- scripts/verify_e2e.py：真实 LLM 端到端验收（4 场景）

**涉及文件**：
- `workflows/main_graph.py`、`nodes/generator.py`、`nodes/rag.py`、`services/llm/prompts.py`
- `app/api/v1/conversations.py`、`app/main.py`
- `tests/workflows/test_full_graph.py`、`tests/workflows/test_phase1_graph.py`、`tests/nodes/test_compliance.py`
- `scripts/verify_e2e.py`

**验证方式（真实 DeepSeek LLM 端到端，4 场景）**：
- multi_step"我做了阑尾炎手术能赔多少"：intent=multi_step → 2 步计划（medical 66s 调 diagnosis_matcher+RAG×2 / claim 10s）→ synthesize 整合 → compliance PASS；回答正确指出缺少保单号并给出补充清单
- simple_faq"阑尾炎手术有等待期吗"（同会话第二轮）：RAG 检索 4 条（top-1 等待期规则）→ 回答含"等待期 30 天"；F14 多轮上下文连贯（第二轮历史含第一轮消息）
- F14 重启恢复：重建图实例 + 共享 checkpointer → 追问"刚才我说做了什么手术"→ 正确引用历史（阑尾炎）；期间真实触发 MODIFY 闭环（LLM 草稿含未脱敏身份证号 risk=30 → 修订后脱敏为 1101********8888 → 复审 PASS），F10/F11 在完整链路自然生效
- chitchat"你好"：ReAct 直答路径正常
- `uv run pytest tests -q` → 214 passed（累计）；`uv run ruff check` → All checks passed

**状态**：✅ 通过验证

**问题与修正**：
- RAG 检索故障测试初版断言错误：rag_node 捕获异常后降级为"无结果"标记继续流程（非直接空 shared_data），synthesize 兜底输出该标记 → 修正断言

### [T022] 端到端测试与场景完善 — 2026-08-25

**操作**：
- nodes/generator.py：补 ReactAgentNode LLM 故障降级（此前唯一无兜底的节点——LLM 超时会导致 A06 500）：bind/ainvoke 异常 → 返回降级话术 + 追加 AIMessage 保证条件边正常终止（末尾非 ToolMessage，不再循环）
- tests/api/test_a06_scenarios.py：A06 端到端场景测试 6 用例（httpx AsyncClient + 真实工具链 + 真实内存 DB + 完整主图，LLM 全 mock）：
  1. 正常 multi_step：完整响应结构（intent/agent_steps×2/compliance_status/used_tools）+ 审计落库（intent/agent_steps/compliance_status/tool_trace 四字段）
  2. 边界·保单不存在：react 路径真实调 policy_query 打空库 → success=false 结构化错误轨迹 + 兜底回答
  3. 异常·LLM 全线超时：intent 关键词兜底（single_domain）→ react 降级话术 → compliance 确定性兜底 PASS，接口 200 不报错
  4. 异常·合规 REJECT：违规内容不返回用户 + need_human_intervention + 会话 transferred + 审计落安全话术（非违规原文）
  5. 异常·合规 MODIFY：修订闭环后返回修订版（"保证赔付"消除，终态 PASS）
  6. 边界·多轮状态隔离：第二轮 agent_steps 不跨轮累积（每轮重置语义），审计 4 条消息

**涉及文件**：
- `nodes/generator.py`（ReactAgentNode 降级补齐）
- `tests/api/test_a06_scenarios.py`

**验证方式**：
- `uv run pytest tests -q` → **220 passed**（累计；本任务 +6）；`uv run ruff check`（含 ui）→ All checks passed
- 验收四场景覆盖核对：保单不存在（场景 2）/ LLM 超时（场景 3，含新增 react 降级）/ 合规拦截（场景 4+5）/ Mock 兜底（T020 test_ocr_extract.py 已覆盖：vision 故障 → mock_fallback 接口 200）
- Mock 数据边界核查：policies（active×3/expired/surrendered）+ medical_records + ocr_fallback 已覆盖正常/异常/边界，无需新增

**状态**：✅ 通过验证

**测试体系总览（T022 时点）**：
- 220 个用例：core 7 / db 7 / api（health+CRUD+A06 场景）26 / llm 7 / tools（基础设施+claim+medical+compliance）69 / rag 11 / agents 14 / nodes（intent+planner/executor+compliance）55 / workflows（phase1+full graph）24
- 三层结构：纯函数与工具单测（mock 外部）→ 图级集成（mock LLM 真实节点）→ API 端到端（AsyncClient + 真实工具链）；真实 LLM 验收脚本 6 个（verify_intent/rag/ui/planner/compliance/ocr/e2e）

### [T023] README 与最终验证（含项目更名）— 2026-08-25（全部 23 任务完成）

**操作**：
- 项目更名 claim-agent → claimflow（D013：与内部 Claim Agent 撞名）：pyproject.toml root 包名 + uv.lock 重新生成（claim-agent v0.1.0 → claimflow v0.1.0）；ci.yml 镜像名 claimflow:ci；app/main.py FastAPI title；README 全部按 claimflow 撰写；数据库名 claim_agent 为内部标识不动
- .github/workflows/ci.yml：lint 范围修正——原只覆盖 app/tests/services/schemas，补齐 nodes/agents/tools/workflows/scripts/ui（与本地验证口径一致）
- README.md：CI 徽章 / 核心能力表 / mermaid 架构图 / 技术栈 / 快速开始（dev 零容器 + Docker 双路径，含 HF_HUB_OFFLINE 提示）/ API 文档（A02-A07 + 响应结构示例）/ 测试说明 / 项目结构 / 设计要点 / MVP 边界
- scripts/verify_ui.py：扩展上传链路验收（F13 补齐）——PIL 生成诊断证明图 → upload_image 回调 → A07 → OCR → 展示断言
- F01-F14 逐条核验：全部通过（核验清单见会话记录；F13 上传演示由本次 verify_ui 扩展补齐）

**涉及文件**：
- `README.md`、`.github/workflows/ci.yml`、`pyproject.toml`、`uv.lock`、`app/main.py`
- `scripts/verify_ui.py`、`.agent/decisions.md`（D013）

**验证方式（真实后端 + 真实 LLM/vision）**：
- 后端以新包名 claimflow 启动正常（uvicorn 重建包 + 9 工具注册）
- `uv run python -m scripts.verify_ui` → 第一轮"保单 POL-2025-0001 住院花了15800元能赔多少"走完整主图（intent=multi_step → 2 步计划 → synthesize → 合规）回答含 4640 元计算明细与工具轨迹；第二轮追问免赔额正确引用上下文（1 万元）；第三轮上传 PIL 诊断证明图 → vision 真实识别四字段全对（张伟/急性阑尾炎/15800.0/2026-08-10，source=vision）→ F13 完整闭环
- `uv run pytest tests -q` → 220 passed；`uv run ruff check`（全目录）→ All checks passed；`docker compose config -q` → 通过
- push 后 CI 全绿：待用户创建 GitHub 仓库 claimflow 后推送确认（本地已按 CI 同口径验证）

**状态**：✅ 通过验证（CI push 确认待执行）

**问题与修正**：
- verify_ui 第二轮断言未匹配"1 万元"（空格）→ 断言加 replace(" ", "")
- BGE-M3 加载时 HuggingFace HEAD 版本检查超时（WinError 10060 × 5 次重试 ≈ 100s+）导致请求超 UI 客户端 180s 超时 → 后端以 HF_HUB_OFFLINE=1 重启（模型已缓存）解决；README 已补提示
- README 初稿"10 个工具"实为 9 个（claim 3 + medical 3 + compliance 3）→ 修正

**待用户执行的收尾步骤**：
1. GitHub 创建空仓库 claimflow（不加 README）
2. `git remote add origin https://github.com/Soleil1043/claimflow.git && git push -u origin main`
3. 确认 Actions 两 job（lint-test / docker）全绿
4. 本地目录改名 d:\Code\PythonProjects\claim-agent → claimflow（改后重开工作区）

### [FIX] CI docker job 镜像名不匹配 — 2026-08-25

**操作**：
- 首次 push 后 CI docker job 失败：`docker build -t claimflow:ci .` 构建的镜像 tag 与 compose 期望的镜像名 `claimflow-app:latest`（`build: .` 未指定 `image:` 时的默认命名）不一致，`docker compose up -d --no-build` 找不到镜像报 `No such image: claimflow-app:latest`
- 修复：docker-compose.yml app 服务显式声明 `image: claimflow:ci`，与 CI 构建产物对齐

**涉及文件**：
- `docker-compose.yml` — app 服务增加 `image: claimflow:ci`

**验证方式**：
- 本地 `docker compose config -q` 通过；push 后 CI docker job 以 `--no-build` 复用 `claimflow:ci` 镜像启动全栈

**状态**：✅ 待 CI 确认

**Git**：`fix: compose app 镜像名与 CI 构建产物对齐`

**CI 确认（2026-08-25）**：push `0e64e95` 后 Actions run #2 两 job（lint-test / docker）全绿，镜像名修复验证通过。

### [T024] Prometheus 指标埋点 — 2026-08-25

**操作**：
- 新增 `services/observability/metrics.py`：三类指标定义（工具：calls_total[tool,status]/latency/breaker_rejected；LLM：calls_total[model,status]/latency/tokens_total[model,kind]；业务：turns_total[intent]/human_interventions/compliance_verdicts[verdict]/turn_latency）+ 容错打点函数（埋点异常不影响业务）
- 新增 `services/observability/llm_metrics.py`：`observed_ainvoke()` 统一包装 LLM 调用（计时 + usage_metadata 提取 token；异常原样抛出由各节点既有降级逻辑处理）
- 埋点接入：ToolExecutor（success/fallback/error 三态 + 熔断拒绝）、A06 send_message（轮次意图/端到端耗时/合规三态/转人工）、7 处 LLM 调用点（intent/planner/generator×2/compliance×2/runner/ocr）
- `app/main.py`：`GET /metrics` 端点（prometheus_client.generate_latest，全局 REGISTRY）
- 依赖：uv add prometheus-client

**涉及文件**：
- `services/observability/metrics.py`、`services/observability/llm_metrics.py`（新增）
- `tools/executor.py`、`app/api/v1/conversations.py`、`app/main.py`、`nodes/intent.py`、`nodes/planner.py`、`nodes/generator.py`、`nodes/compliance.py`、`agents/runner.py`、`tools/medical/ocr_extract.py`
- `tests/observability/test_metrics.py`（新增，9 用例）、`pyproject.toml`、`uv.lock`

**验证方式**：
- `uv run python -m pytest tests -q` → 229 passed（原 220 + 新增 9）
- `uv run ruff check`（全目录）→ All checks passed
- 单测覆盖：指标注册/标签维度/token 缺失不记/转人工计数/executor 三态+熔断/observed_ainvoke 成功与异常传播//metrics 端点文本协议

**状态**：✅ 通过验证

**Git**：`feat: T024 Prometheus 指标埋点（工具/LLM/业务三类指标 + /metrics 端点）`

### [T025] Prometheus + Grafana 容器化与仪表盘 — 2026-08-25

**操作**：
- 监控栈独立 profile（D016 方案 B）：`docker compose --profile monitoring up -d` 才启动，默认 `up` 与 CI 不受影响（默认 4 服务 / 带 profile 6 服务，`docker compose config --services` 双向验证）
- `prometheus/prometheus.yml`：15s 抓取 `app:8000/metrics`（compose 服务名 DNS）
- Grafana 声明式 provisioning：`grafana/provisioning/datasources/prometheus.yml`（数据源自动注册，URL 指向 prometheus:9090）+ `grafana/provisioning/dashboards/provider.yml`（挂载目录自动加载/热重载）
- `grafana/dashboards/claimflow-overview.json`：10 面板覆盖 T024 全指标——工具成功率/人工转接率/合规三态分布/单轮 P95（stat+donut）+ 工具 P95 延迟/调用量堆叠/LLM 延迟/Token 消耗/轮次速率/熔断拒绝（timeseries）
- `docker-compose.yml`：新增 prometheus（v3.1.0）+ grafana（11.5.2）服务，挂载配置只读 + prometheus_data/grafana_data 卷；演示场景匿名 Admin 免登录（注释标注生产需移除）

**涉及文件**：
- `prometheus/prometheus.yml`、`grafana/provisioning/datasources/prometheus.yml`、`grafana/provisioning/dashboards/provider.yml`、`grafana/dashboards/claimflow-overview.json`（新增）
- `docker-compose.yml`

**验证方式（本地容器化实测，全链路）**：
- `docker compose config -q` / `--profile monitoring config --services` → 默认 4 服务、profile 6 服务，隔离正确
- `--profile monitoring up -d` 六容器全部 Up；Prometheus targets API：`claimflow-app -> up (http://app:8000/metrics)`，`up{claimflow-app} = 1`
- 真实业务链路：POST A06 发消息（intent=simple_faq, compliance=PASS）→ 35s 后 Prometheus 查询 `claimflow_conversation_turns_total{intent="simple_faq"} = 1`，指标入库
- Grafana：health ok（11.5.2）、API 检索到自动加载的仪表盘 uid=claimflow-overview，10 面板（4 stat/donut + 6 timeseries）全部就位
- 验证完毕 `--profile monitoring down -v` 清理

**状态**：✅ 通过验证

**Git**：`feat: T025 Prometheus + Grafana 容器化与仪表盘（monitoring profile）`

### [T026] 评测数据集构建（200 条）— 2026-08-25

**操作**：
- `evals/schemas.py`：EvalCase（用户输入 + expected_tools + 判分要点 must_include/any_of/must_not_include + expect_human_intervention + note 溯源字段）+ EvalCategory 四分类 + EvalDataset；schema 防呆（无判分要点用例直接拒绝）
- `scripts/gen_eval_dataset.py`：数据集生成脚本（表驱动 + 模板×参数组合），产出 `evals/datasets/eval_dataset.json`（v1.0.0，200 条）
- 配比精确达标：FAQ 30 / 单领域 60（POL 20 + MED 20 + CMP 20）/ 多步 80（计算锚点 12 + 模板组合 40 + 长尾 28）/ 边界 30
- 期望值全量溯源：计算锚点 4640 元 ← kb_docs/03 计算示例；等待期/免责/材料 ← kb_docs 对应文档；保单/就诊数据 ← data/mock/*.json；每条 note 标注来源
- `tests/evals/test_dataset.py`：8 用例校验（规模/配比/ID 唯一前缀/标注质量/计算锚点≥3/合规红线≥4/期望工具均为注册名/schema 防呆）

**涉及文件**：
- `evals/schemas.py`、`scripts/gen_eval_dataset.py`、`evals/datasets/eval_dataset.json`（新增）
- `tests/evals/test_dataset.py`（新增）

**验证方式**：
- `uv run python -m scripts.gen_eval_dataset` → 200 条，分类计数 {faq:30, single:60, multi:80, edge:30}
- `uv run python -m pytest tests -q` → 237 passed（原 229 + 新增 8）
- `uv run ruff check`（含 evals 目录）→ All checks passed

**状态**：✅ 通过验证

**Git**：`feat: T026 评测数据集构建（200 条，期望值全量溯源）`

### [T027] 评测运行器与指标计算 — 2026-08-25

**操作**：
- `evals/metrics.py`：纯函数判分层——score_case（must_include 全包含/any_of 任一/must_not_include 红线/工具子集匹配/转人工一致，归一化容错"30 天/4,640/10,000"格式差异）+ aggregate（任务完成率/工具准确率/合规通过率/平均耗时/分类分桶）+ result_from_a06 适配层
- `evals/test_suite.py`：运行器——构建主图（真实 LLM + dev profile 零容器）→ 逐条独立 thread ainvoke → 判分 → 聚合 → JSON 落盘；支持 --category/--limit/--out
- `tests/evals/test_scoring.py`：20 用例覆盖判分全规则

**基线报告（真实 LLM 全量 200 条，evals/reports/baseline.json）**：
- 任务完成率 89.5%（179/200，含判分归一化修复后 3 条翻转）
- 工具调用准确率 95.3% / 合规通过率 99.5%（红线违规 0）/ 平均耗时 19.0s
- 分类：FAQ 93.3% / 单领域 78.3% / 多步 92.5% / 边界 90.0%
- 剩余 21 条失败全部为 LLM 表述差异（any_miss/must_miss）与漏调工具（tool_miss），无一条红线/转人工错误

**问题与修正**：
- 评测直调图时 tool_trace 未转 used_tools（55 条误判）→ 运行器补 A06 同口径转换
- 判分归一化未去千分位逗号（"10,000"≠"10000"）→ _norm 补 replace(",","")，重放验证翻转 3 条
- 第二轮跑基线时 DeepSeek 账户余额耗尽（402）导致 multi_step 假崩（3.8%）→ 用户充值后重跑得真实水位 89.5%
- BGE-M3 HuggingFace HEAD 检查超时 → 评测会话需 HF_HUB_OFFLINE=1（T023 已知）
- tests/evals/test_metrics.py 与 tests/observability/test_metrics.py 同名冲突（无 __init__.py）→ 改名 test_scoring.py

**涉及文件**：
- `evals/metrics.py`、`evals/test_suite.py`、`evals/reports/baseline.json`（新增）
- `tests/evals/test_scoring.py`（新增，20 用例）

**验证方式**：
- `uv run python -m pytest tests -q` → 249 passed；ruff 全绿
- 小样本 --limit 8 → 7/8（LLM 表述波动 1 条）
- 全量 200 条 → 89.5%，失败明细可从报告 failures 字段溯源

**状态**：✅ 通过验证

**Git**：`feat: T027 评测运行器与指标计算（基线 89.5%）`

### [T028] Redis 工具结果缓存 — 2026-08-25

**操作**：
- `services/cache.py`：ToolCacheBackend 协议 + Redis 后端（prod，redis.asyncio）/ MemoryToolCache（dev，TTL 字典语义对齐）/ _NoopBackend（禁用态）+ ToolResultCache 门面（canonical json sha256 指纹 key：claimflow:toolcache:{tool}:{digest}）
- `tools/executor.py`：execute() 接入缓存——白名单工具先查缓存（命中直接返回，不计入熔断/耗时统计，只记缓存指标），成功结果回写（success=False 不缓存，防止失败态被固化）
- `services/observability/metrics.py`：新增 claimflow_tool_cache_hits_total{tool,result=hit|miss}
- 配置：TOOL_CACHE_ENABLED / TOOL_CACHE_TTL_SECONDS（默认 300s）/ TOOL_CACHE_TOOLS（白名单：policy_query、medical_record_query、diagnosis_matcher、claim_rule_rag、claim_status_query——全部纯读查询，计算/合规工具不入缓存）；.env.example 同步
- `app/main.py`：关停时释放缓存连接

**涉及文件**：
- `services/cache.py`、`tests/tools/test_tool_cache.py`（新增，10 用例）
- `tools/executor.py`、`services/observability/metrics.py`、`app/core/config.py`、`app/main.py`、`.env.example`

**验证方式**：
- 单测覆盖：命中/入参不同 miss/过期（篡改过期时间模拟）/key 键序无关/禁用后端（Noop 永不命中）/白名单解析/executor 二次调用真实执行仅 1 次/非白名单不缓存/miss→hit 指标/失败结果不缓存
- `uv run python -m pytest tests -q` → 259 passed（原 249 + 新增 10）；ruff 全绿

**问题与修正**：
- test_cache_disabled_backend 全量跑失败：test_logging.py 的 `importlib.reload(config_module)` 产生新 settings 单例，`services.cache` 仍持旧引用——改 `app.core.config.settings` 对本模块不生效（跨实例陷阱）。修正：monkeypatch.setattr 到 `services.cache` 模块实际引用的 settings 对象。此坑记录备查：任何 reload app.core.config 的测试都会造成 settings 双实例。

**状态**：✅ 通过验证

**Git**：`feat: T028 Redis 工具结果缓存（幂等工具白名单 + 命中指标 + dev 降级）`

### [T029] Token 消耗统计与预算控制 — 2026-08-25

**操作**：
- `services/observability/token_tracker.py`：TurnTokenTracker（环节→模型→[prompt, completion] 分桶归集）+ contextvars 跨节点传递（A06 入口 start_turn_tokens / 出口 finish_turn_tokens / track_phase 环节标注 / phase_ainvoke 组合包装）
- 归集链路：observed_ainvoke 成功后回调 record_usage_to_tracker（usage_metadata 自动提取，节点零侵入）
- 环节接入：intent / planner / generator（executor+generator 两处）/ compliance（审查+修订）/ runner（Worker ReAct）/ ocr 共 7 处 phase_ainvoke 替换
- `app/api/v1/conversations.py` A06：入口创建 tracker，出口 finish（结构化日志 turn_tokens_summary + Prometheus 分环节指标 + 超预算 warning turn_token_budget_exceeded 不阻断）
- `metrics.py`：新增 claimflow_turn_tokens_total{phase, model}；配置 TURN_TOKEN_BUDGET（默认 0=不设预算），.env.example 同步

**涉及文件**：
- `services/observability/token_tracker.py`、`tests/observability/test_token_tracker.py`（新增，11 用例）
- `services/observability/llm_metrics.py`、`services/observability/metrics.py`、`app/api/v1/conversations.py`、`app/core/config.py`、`.env.example`
- `nodes/intent.py`、`nodes/planner.py`、`nodes/generator.py`、`nodes/compliance.py`、`agents/runner.py`、`tools/medical/ocr_extract.py`（observed_ainvoke → phase_ainvoke）

**验证方式**：
- 单测覆盖：分环节分模型归集/同环节累计/上下文归集/无 tracker 无操作/嵌套 phase 恢复/超预算 warning（含预算值）/正常 info 汇总/Prometheus 分环节指标/finish 后上下文清空/phase_ainvoke 组合链路
- `uv run python -m pytest tests -q` → 269 passed（原 259 + 新增 11 但 metrics 测试合并后 268+1，全绿）；ruff 全绿

**问题与修正**：
- 预算超限测试全量跑失败：同 T028 的 settings 双实例陷阱（test_logging reload 产生新单例，token_tracker 持旧引用）→ monkeypatch.setattr 到模块引用修复。该陷阱已在两处出现，后续任何"改配置验证行为"的测试都应 monkeypatch 到消费方模块。

**状态**：✅ 通过验证

**Git**：`feat: T029 Token 消耗统计与预算控制（分环节归集 + 超限告警）`

### [T030] Phase 3 收尾验证 — 2026-08-25（Phase 3 全部完成）

**操作**：
- README 补齐 Phase 3 章节：
  - 核心能力表新增：可观测性 / 评测体系 / 工具结果缓存三行
  - 快速开始新增"5. 监控栈"：`--profile monitoring` 启动、Grafana/Prometheus 访问方式、10 面板清单、/metrics 裸访问
  - 新增"评测体系"章节：200 条测试集说明、运行命令（--category/--limit/--out + HF_HUB_OFFLINE 提示）、基线报告指标表（89.5%/95.3%/99.5%）、判分规则说明
  - 测试数更新 220→269；项目结构补 grafana/prometheus；范围说明改为 Phase 4 边界（GraphRAG/A-B/OTel）
- 全量验证：ruff 全绿、pytest 269 passed、docker compose 双模式（默认/monitoring）config 校验通过

**涉及文件**：
- `README.md`、`.agent/tasks.md`

**验证方式**：
- `uv run ruff check`（全目录）→ All checks passed
- `uv run python -m pytest tests -q` → 269 passed
- `docker compose config -q` + `--profile monitoring config -q` → 通过
- 评测基线报告已存档 `evals/reports/baseline.json`（T027 产出）
- push 后 CI 两 job 全绿（待确认）

**状态**：✅ 通过验证（CI push 确认待执行）

**Git**：`feat: T030 Phase 3 收尾验证（README 监控/评测章节 + 全量验证）`

**CI 确认（2026-08-25）**：push `747cdd9` 后 Actions run #4 两 job（lint-test 269 测试 / docker compose 校验）completed/success，Phase 3 全部验收通过。

**Phase 3 交付总览**：
- T024 Prometheus 指标埋点（工具/LLM/业务三类 + /metrics 端点）
- T025 Prometheus + Grafana 容器化（monitoring profile + 10 面板自动加载，本地全链路实测）
- T026 评测数据集 200 条（期望值全量溯源，schema 防呆校验）
- T027 评测运行器（基线报告 89.5%，判分归一化容错）
- T028 工具结果缓存（Redis/dev 内存降级 + 命中指标 + 失败不缓存）
- T029 Token 统计与预算（contextvars 分环节归集 + 超限告警）
- T030 收尾（README 双章节 + 全量验证 + CI）

### [T031] 知识图谱构建 — 2026-08-26

**操作**：
- `services/rag/knowledge_graph.py`：GraphEntity（id 前缀强校验）/ GraphRelation / KnowledgeGraphData schema + KnowledgeGraph 内存图（邻接表 + 反向邻接 + 实体索引；neighbors/find_entities/multi_hop BFS/stats 接口）+ build_graph_from_triples（实体去重、非法三元组跳过容错）+ save/load_graph 落盘回读
- `services/llm/prompts.py`：KG_EXTRACTION_PROMPT（三类实体/四种关系/抽取纪律）
- `scripts/build_kg.py`：12 篇 kb_docs 逐篇 LLM 抽取（解析失败重试 1 次再跳过）→ 汇总去重 → 落盘 data/graph/claim_rules_kg.json（幂等全量重建）
- `tests/rag/test_knowledge_graph.py`：9 用例（schema 校验/邻接正反向/实体查找/多跳/统计/三元组容错/落盘往返/缺文件空图兜底）

**实际构建结果（deepseek-v4-flash，116 三元组）**：
- 实体 106（insurance 8 / rule 67 / disease 31），关系 116（applies_to_rule 48 / excludes 32 / covers 26 / disease_rule 10），平均度 2.19
- 关键链路验证：急性阑尾炎 ←covers— 安心医疗旗舰版（反向邻接可用，T032 疾病→险种→规则多跳检索路径成立）

**问题与修正**：
- 第一轮构建疾病实体全丢（disease 0 个，LLM 把疾病塞进 rule 名字/前缀写错被静默丢弃）→ prompt 强化（【疾病必须建成 disease 实体】+ ICD 表逐行建关系 + id 前缀丢弃警告），第二轮修复：disease 31 个/covers 26 条
- 构建慢的根因：client timeout 60s < 大 JSON 生成时间 → 超时→openai 内建重试×2→脚本级重试叠加，单篇最坏 3-6 分钟（总 45 分钟）。已知优化点（timeout 180s + 并发 3）留作 build 脚本后续改进，不阻塞 T031 验收
- 小瑕疵（可接受）：险种存在别名实体（"医疗险"与"安心医疗旗舰版"并存），检索侧模糊匹配可消化

**验证方式**：
- `uv run python -m pytest tests -q` → 278 passed（原 269 + 新增 9）；ruff 全绿
- 图谱统计/疾病多跳抽查通过（见上）

**状态**：✅ 通过验证

**Git**：`feat: T031 知识图谱构建（106 实体/116 关系，LLM 抽取 + 幂等重建）`

### [T032] 图检索与混合召回 — 2026-08-26

**操作**：
- `services/rag/graph_retriever.py`：实体链接三级匹配（完整子串 / 简写子串 / bigram 重叠率≥50% 跳词容忍）+ 正反向 BFS ≤2 跳扩展（`_facts_along_path` 相邻对自动识别正/反向边，事实主语保持关系源点）+ `search_graph` 入口（图谱惰性单例；禁用/缺文件/未命中均零影响降级）+ 事实上限 12 条（Token 控制）
- `services/rag/knowledge_graph.py`：`multi_hop` 增加 `reverse=True` 入边遍历（covers 是 险种→疾病 方向，"XX 病能赔吗"必须沿入边回溯到险种——正向-only 是设计缺口，实测发现后补）
- `nodes/rag.py`：rag_node 混合召回接入，`graph_facts` 并入 `rag_context`（向量与图两路信号同写 shared_data 供 synthesize 消费；空结果条件改为 `not chunks and graph_facts is None`）
- `tools/claim/claim_rule_rag.py`：Worker 路径同步接入（输出 `graph_facts` 维度）
- `app/core/config.py`：`graph_rag_enabled: bool = True`（GRAPH_RAG_ENABLED 开关）
- `tests/rag/test_graph_retriever.py`：14 用例（链接三级 6 测 / 双向扩展 5 测 / search_graph 冒烟+开关+缺文件 3 测）

**实测（真实图谱 106 实体，5 个复杂关联问题）**：
- "急性阑尾炎手术能赔吗" → 等待期规则 + 医疗险/安心旗舰版 covers（反向回溯生效）
- "安心医疗旗舰版哪些疾病不保" → 跳词简写命中险种，扩展 12 条适用规则（等待期/免赔额/赔付比例）
- "阑尾炎住院报销比例是多少"（疾病简写）→ bigram 匹配命中急性阑尾炎 → 等待期 + covers
- 5/5 命中且事实相关；附带噪声（"无等待期"实体被"等待期"类查询带出）可接受

**问题与修正**：
- 初版只走正向邻接，疾病查询（covers 的 target）扩展不出险种事实 → `multi_hop` 加 reverse + expand 双向 BFS
- 实体链接对跳词简写不鲁棒（"安心医疗旗舰版"≠实体名"安心医疗保险（旗舰版）"、"阑尾炎"≠"急性阑尾炎"）→ 第三级 bigram 重叠匹配（分母为实体名 gram 数，防长名误配）
- 存量测试 `test_simple_faq_rag_error_not_fatal` 语义更新：Qdrant 故障时本地图谱仍补充事实（混合召回增强而非回归），断言改为"0 条条款 + graph_facts 存在"
- 测试 mini_graph 最初实体 name 写成 id 形态（"K35急性阑尾炎"），与 T031 真实数据（name 干净、ICD 在 properties）不符 → 对齐真实数据格式

**验证方式**：
- `uv run python -m pytest -q` → 292 passed（原 278 + 新增 14）；ruff check/format 全绿（T032 涉及文件）
- 复杂关联问题实测 5/5 命中（见上）

**状态**：✅ 通过验证

**Git**：`feat: T032 图检索与混合召回（实体链接三级匹配 + 双向 BFS + GRAPH_RAG_ENABLED 开关）`

### [T033] GraphRAG 评测对比 — 2026-08-26

**操作**：
- `evals/schemas.py`：EvalCategory 新增 GRAPH_ASSOC（graph_assoc 关联类，独立数据集不占主数据集四分类配比）
- `evals/datasets/eval_graph_assoc.json`：24 条复杂关联用例（疾病↔险种↔规则多跳），must/any_of 全部 kb_docs 可溯源，GA-011 保留 kb03 计算锚点 4640
- `evals/metrics.py`：CaseResult 增 vector_hits/graph_hits（rag_node 从 rag_context、Worker 从 tool_trace.output.data 两路提取）；EvalReport 增 avg_vector_hits/avg_graph_hits/graph_coverage
- `evals/test_suite.py`：`--dataset {main,graph_assoc}` + `--variant {hybrid,pure_rag}`（变体即 GRAPH_RAG_ENABLED 开关，运行前改 settings + reset 图谱单例）；报告落盘带 dataset/variant 字段
- `tests/evals/test_graph_assoc_dataset.py`：4 用例（规模/ID/溯源/真实图谱实体链接命中 ≥15/24）
- `tests/evals/test_dataset.py`：配比断言改为非零分类比对（graph_assoc 独立数据集）
- `evals/reports/graph_assoc_{pure_rag,hybrid}.json` + `graph_assoc_comparison.md`：对比报告存档

**评测结果（24 条，deepseek-v4-flash）**：
- 任务完成率：pure_rag 95.8%（23/24）= hybrid 95.8%（23/24）持平；单条失败均为 LLM 输出随机波动（两轮失败用例不同）
- 检索命中差异：向量均 3.83 条/例（混合不损害向量）；hybrid 图谱覆盖 87.5%、+6.92 条结构化事实/例
- 耗时：9.9s → 12.3s（+2.4s 实体链接+BFS 开销）
- 结论：小语料（12 篇）下完成率持平，增益在检索信号维度（跨文档聚合问题图谱直接给规则边）；语料扩大后增益预期放大

**问题与修正**：
- 首轮表面差距（pure 91.7% vs hybrid 79.2%）逐条归因全部为判分词面未覆盖同义表述（"没有等待期"≠"无等待期"、"可申请理赔"≠"可赔"）→ 修订 5 条 any_of 后复跑两组持平。教训：any_of 标注必须含"申请理赔/申请赔付"类规范变体
- huggingface_hub 联网 HEAD 检查每文件 5×30s 超时重试导致评测启动卡 ~6 分钟 → 跑评测须设 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1（本地缓存已有 BGE-M3）
- 观察到图谱噪声：泛"等待期"查询实体链接带出最多 12 条不相关规则事实，有上下文稀释风险——优化方向（关系类型过滤/实体类型加权/动态上限）留作后续

**验证方式**：
- `uv run python -m pytest -q` → 296 passed（原 292 + 新增 4）；ruff check/format 全绿
- 两组评测命令见 graph_assoc_comparison.md，报告 JSON+MD 存档 evals/reports/

**状态**：✅ 通过验证

**Git**：`feat: T033 GraphRAG 评测对比（24 条关联用例 + --variant 变体 + 双组报告存档）`

### [T034] 长期记忆写路径 — 2026-08-26

**操作**：
- `services/memory/long_term.py`（新建）：MemoryRecord（pydantic）+ summarize_conversation（LLM 结构化摘要 MEMORY_SUMMARY_PROMPT，非法输出/异常降级确定性正则提取：保单号/金额 + 尾部对话粗摘要，函数永不抛错）+ write_memory（摘要+实体拼接文本 BGE-M3 向量化 → Qdrant 独立 collection，payload 含 user_id/conversation_id/entities/turn_count/source）+ maybe_write_memory（A06 出口入口：每 N 轮触发、转人工终态 force 快照、旁路容错全吞错）
- 幂等设计：point id = uuid5(conversation_id) 确定性——一会话一条记忆，重复写 upsert 覆盖（摘要始终反映会话最新全貌），不产生重复条目
- `app/api/v1/conversations.py` A06 出口：metrics 埋点后、token 汇总前调用 maybe_write_memory（force=need_human），摘要 LLM 的 token 计入本轮 memory 环节
- `services/llm/prompts.py`：MEMORY_SUMMARY_PROMPT（摘要 100-200 字事实性 + 三类实体 JSON 输出）
- 配置：memory_enabled / memory_summary_every_n_turns（默认 3）/ qdrant_memory_collection（默认 long_term_memory），.env.example 同步
- `services/observability/metrics.py`：claimflow_memory_writes_total{result=success|error}
- `tests/conftest.py`（新建）：autouse 默认关闭记忆写（防 A06 场景测试 REJECT force 触发真实 BGE-M3 加载与 ./data/qdrant 写入；patch 打在 long_term 模块引用的 settings 对象上，规避 T028/T029 的双实例陷阱）
- `tests/memory/test_long_term.py`（新建，17 用例）：正则提取 2 / 消息过滤与轮数 / point id 确定性 / 摘要 LLM 主路径+非法输出兜底+异常兜底+空消息 / 写入建 collection+payload / 幂等覆盖 / 用户隔离 / schema 防呆 / 触发阈值+force+禁用+零轮+写入故障不抛
- `tests/api/test_a06_scenarios.py`：+1 场景（REJECT 终态 A06 出口以 force=True 调用记忆写路径，spy 验证接线）

**验证方式（真实 DeepSeek LLM + 真实 BGE-M3，scripts/verify_memory.py）**：
- 摘要质量：3 轮张伟理赔对话 → LLM 完整提炼保单 POL-2025-0001/急性阑尾炎/15800/免赔 10000/预估 4640/等待期结论/材料清单；实体 {policy_nos: [POL-2025-0001], diagnoses: [急性阑尾炎], amounts: [4640, 10000, 15800]} 全对
- 入库：独立 collection long_term_memory，payload.user_id=demo-user-001
- 幂等：同会话重复写 1→1 条（upsert 覆盖）
- 隔离：按 user_id filter 检索"我上次问的那张保单"命中本人记忆（score 0.671）；其他 user_id 检索 0 命中
- `uv run python -m pytest -q` → 314 passed（原 296 + 新增 18）；ruff check 全绿、涉及文件 format 全绿

**问题与修正**：
- Qdrant local mode 的 query_filter 必须传强类型 models.Filter 对象（服务端接受裸 dict，local mode 严格抛 'dict' object has no attribute 'must'）——T035 读路径实现时注意
- 验证脚本首版用随机 uuid4 会话 id，重跑残留旧数据导致幂等计数误报 → 改固定演示会话 id + 脚本开头按 user_id 定点清理演示数据

**状态**：✅ 通过验证

**Git**：`feat: T034 长期记忆写路径（每 N 轮摘要 + 实体提取 + 幂等入库 + user_id 隔离）`

### [T035] 长期记忆读注入 — 2026-08-26

**操作**：
- `services/memory/long_term.py`：读路径——MemoryHit + search_memories（BGE-M3 向量化查询 + user_id 强类型 Filter（T034 实测 local mode 不收裸 dict）+ memory_min_score 噪声过滤；禁用/collection 缺失/异常一律空列表直跳，永不抛错）+ format_memory_context（多条合并、总长 1200 字符截断——Token 预算控制）
- `state.py`：新增 memory_context 字段（A06 首轮写入、各回答节点消费，空串=不注入）
- 注入点三处（覆盖全部回答出口路径）：
  - `nodes/generator.py` ReactAgentNode._system_prefix：memory_context 非空时 system prompt 附加"用户历史会话记忆"段（临时拼接不写回 checkpoint messages）
  - `nodes/generator.py` synthesize_answer_node：ANSWER_SYNTHESIS_PROMPT 新增 {memory} 段（空时传"无"保持原语义）
  - `nodes/step_executor.py`：worker 指令附加记忆段——multi_step 路径 Worker 也能理解"上次问的那张保单"类指代（验收问句"能赔多少"intent=multi_step，只注入 generator 会漏掉此路径）
- `app/api/v1/conversations.py` A06 入口：仅新会话首轮（本会话无 user 消息，DB count 判断）检索注入；非首轮本会话上下文已在 checkpoint 不再检索；无历史检索空 → memory_context="" 零影响
- 配置：memory_top_k（默认 2）/ memory_min_score（默认 0.4），.env.example 同步
- 测试：tests/memory/test_long_term.py +7（user_id filter 隔离（u2 同向点不带回）/min_score 正交过滤/无历史空直跳/禁用/collection 缺失/故障吞错/拼装截断）；tests/nodes/test_generator_memory.py 新建 5 用例（react system 注入与空态不变/synthesize 注入与"无"/step_executor 指令附加与原样）；tests/api/test_a06_scenarios.py +3 场景（首轮注入断言 LLM system message 含保单号/无历史零影响/非首轮不再检索）
- `scripts/verify_memory_read.py`：真实 LLM 跨会话验收（会话 A 写记忆 → 新会话完整主图提问 → 对照无历史用户）

**验证方式（真实 DeepSeek LLM + 真实 BGE-M3 + 完整主图）**：
- 检索注入："我上次问的那张保单，最后说能赔多少来着？" 命中本人会话 A 记忆（score 0.697）
- 跨会话连贯（完整主图 intent→…→合规）：回答"您上次咨询的保单 POL-2025-0001（安心医疗保险旗舰版），因急性阑尾炎手术住院费用 15800 元，预估赔付金额为 4640 元。该金额基于免赔额 10000 元、赔付比例 80% 计算得出…"——保单号与金额全部正确引用历史
- 无历史零影响：其他用户同问题检索 0 条直跳，回答"无法直接看到您上一次查询的记录，麻烦您提供保单号或身份证号…"——诚实引导不编造
- `uv run python -m pytest -q` → 329 passed（原 314 + 新增 15）；ruff 全绿、涉及文件 format 全绿

**状态**：✅ 通过验证

**Git**：`feat: T035 长期记忆读注入（首轮检索注入 system prompt + 跨会话引用 + 无历史零影响）`

### [T036] HITL 工单后端 — 2026-08-26

**操作**：
- `services/db/models.py`：HumanTicket 表（conversation_id 索引/user_id 冗余/intervention_reason 拦截原因快照/compliance_snapshot 合规裁决完整快照（verdict/violations/risk_score/reason）/status 状态机/resolution_note 坐席结论/resolved_by/时间戳）
- `alembic/versions/a8e85d881b28_add_human_tickets_table.py`：autogenerate 迁移（含 status/conversation_id 两索引），本地 upgrade 成功 + downgrade/upgrade 往返验证
- `app/api/v1/interventions.py`（新建）：
  - ensure_human_ticket：A06 转人工出口落单，幂等（该会话存在 pending 工单跳过；终态后可再落新单）
  - GET /api/v1/interventions：列表（status 筛选 + 分页 + 倒序，坐席队列）
  - GET /api/v1/interventions/{id}：详情 + 聚合上下文（会话完整轨迹 messages 含 tool_trace/agent_steps/compliance_status + 合规快照 + 拦截原因 + 会话信息）
  - POST /{id}/resolve（回写结论）与 /{id}/escalate（升级转出）：状态机守卫 `_ensure_pending`——终态再流转 409
- `app/api/v1/conversations.py` A06：need_human 时 ensure_human_ticket（快照取自本轮 compliance_result）；`_to_message_item` 公开化为 `to_message_item` 供工单聚合复用
- `schemas/api.py`：HumanTicketSummary/Detail（含 ConversationRef + messages）/Resolve/EscalateRequest
- `app/main.py`：注册 interventions 路由
- `tests/api/test_interventions.py`（新建，9 用例）：落单幂等（open 跳过 + 终态后新单）/列表空态/status 筛选与倒序/详情聚合（tool_trace + agent_steps + 合规快照 + 轨迹）/404/resolve 回写/escalate/终态 409 ×3/校验 422
- `tests/api/test_a06_scenarios.py` 场景 4 扩展：REJECT 后断言 pending 工单自动创建 + 快照 verdict=REJECT + 聚合轨迹完整
- `tests/db/`：表数量断言 6→7（test_models/test_session）

**验证方式**：
- `uv run python -m alembic upgrade head` → human_tickets 建表成功；downgrade -1 + upgrade head 往返通过
- `uv run python -m pytest -q` → 338 passed（原 329 + 新增 9）；ruff 全绿、涉及文件 format 全绿
- A06 集成（场景 4，mock LLM + 真实路由 + 真实 DB）：REJECT → pending 工单落库 → 详情聚合上下文完整

**问题与修正**：
- resolve/escalate 初版 `ticket.updated_at = func.now()`（SQL 表达式赋值）后 flush 再回读触发 lazy refresh，同步上下文报 MissingGreenlet/ClosedDB——A06 未炸因从不回读该字段。修正：动作接口改赋 Python datetime（`dt.datetime.now()`），flush 后回读零 IO
- alembic autogenerate 迁移缺 Text import（JSONB variant 引用，T003 同款坑）→ 手动补

**状态**：✅ 通过验证

**Git**：`feat: T036 HITL 工单后端（状态机 + 聚合上下文 API + 坐席处理动作）`

### [T037] LangGraph interrupt 恢复机制 — 2026-08-26

**操作**：
- `nodes/human_review.py`（新建）：HumanReviewNode——interrupt(payload) 挂起（载荷含拦截原因 + 合规裁决快照）；坐席 Command(resume={"resolution_note","resolved_by"}) 恢复后节点重跑，interrupt() 直接返回结论；结论经 review_answer 复审（F10 门禁对坐席文本同样生效）：PASS/MODIFY → 结论作为 final_answer + 介入闭环（need_human=False）；REJECT → 保守话术（"合规复核中"）；空结论/非 dict resume 值 → 安全话术不抛错
- 关键设计：interrupt 节点独立于 compliance——resume 时整个节点重跑，放 compliance 内会导致 LLM 审查重复执行且结果漂移
- `workflows/main_graph.py`：compliance 三态路由 reject 目标从 END 改为 human_review；human_review → END
- `app/api/v1/conversations.py` A06：transferred（挂起中）会话再发消息 409（防新输入被挂起流程吞掉）；compliance REJECT 仍写安全话术 + 介入标记，A06 行为不变
- `app/api/v1/interventions.py` resolve（T036 扩展）：先 aget_state 预检挂起（snapshot.next）→ Command(resume=坐席结论) 恢复 → 复审后 final_answer 返回；会话 transferred → active；坐席回复落审计（compliance_status=复审 verdict）；图无挂起/恢复异常不阻断工单闭环（answer 回退结论原文，resumed=False）；escalate 不触发恢复
- `schemas/api.py`：TicketResolveResponse（ticket + answer + resumed）
- 测试：`tests/workflows/test_interrupt.py`（新建 7 用例：REJECT 触发挂起（__interrupt__ + 载荷快照）/结论复审 PASS 返回/复审 REJECT 保守话术/空结论/非 dict resume/共享 checkpointer 重建图跨"重启"恢复/无挂起 snapshot.next 预检）；`tests/api/test_a06_scenarios.py` 场景 11（HITL 全链路：REJECT → 挂起期 409 → resolve 恢复 resumed=True + answer=复审结论 → 坐席回复落审计 PASS → 会话回 active 新消息 200）；`tests/api/test_interventions.py` 适配（图桩无挂起走回退路径 + 响应结构嵌套 ticket）

**验证方式**：
- `uv run python -m pytest -q` → 346 passed（原 338 + 新增 8）；ruff 全绿、涉及文件 format 全绿
- 端到端语义验证：触发（REJECT → __interrupt__，final_answer=安全话术）→ 恢复（resume 干净结论 → 复审 PASS → 结论返回，need_human=False）→ 跨重启（共享 checkpointer 重建图 → resume 成功）
- 复审拦截验证：坐席结论"保证赔付一百万"复审 REJECT → 不返回用户，替换为合规复核话术

**问题与修正**：
- resolve 向无挂起 thread 发 Command(resume) 行为不可控 → aget_state 预检 snapshot.next，仅挂起态恢复（无挂起回退结论原文，服务重启丢内存 checkpoint 的 dev 场景不阻断工单闭环）
- 图级测试初版 FakeModel 返回裸对象缺 tool_calls 属性（react 条件边 AttributeError）→ 改返回 AIMessage
- test_circuit_breaker_half_open_recovery 在全量运行时偶发失败（cooldown 时间敏感，负载高时漂移）——与 T037 无关，重跑通过；如再复现考虑放宽断言容差

**状态**：✅ 通过验证

**Git**：`feat: T037 LangGraph interrupt 恢复机制（REJECT 挂起 + 坐席 Command(resume) 恢复 + 复审闭环）`

### [T038] Next.js 人工介入工作台 — 2026-08-26

**操作**：
- `workbench/`（新建目录，Next.js 15 + React 19 + Tailwind 4 + TypeScript，App Router）：
  - 骨架：package.json / next.config.ts（rewrites `/api/*` → localhost:8000，WORKBENCH_API_TARGET 可覆盖）/ tsconfig / postcss（Tailwind 4 @tailwindcss/postcss）；dev 端口 5173
  - `lib/api.ts`：类型定义对齐 schemas/api.py + fetch 封装（浏览器走相对路径代理，RSC 服务端走绝对地址——相对路径在服务端 fetch 不经 rewrites，实测坑）
  - `app/page.tsx` 列表页：状态筛选 tabs（searchParams 驱动 RSC）+ 工单表格（工单号/用户/拦截原因/状态徽章/时间）+ 后端不可达友好提示
  - `app/tickets/[id]/page.tsx` 详情页：工单头部 + 合规拦截快照卡（verdict/风险分配色/违规明细与建议）+ 坐席处理区（pending 表单 / 终态结论展示）+ 会话完整轨迹
  - `components/`：StatusBadge / MessageTimeline（user/assistant 气泡 + intent/compliance 徽章）/ AuditViewer（工具调用入参出参 JSON + Agent 步骤档案，展开式）/ ResolveForm（结论 textarea + 坐席标识 + resolve/escalate，成功后展示恢复结果 + router.refresh）
- `scripts/demo_hitl_backend.py`：mock LLM 演示后端（真实 DB/路由/图；欺诈草稿 → 确定性 REJECT → 工单 + interrupt 挂起）——真实 LLM 对欺诈诱导会正确拒答（PASS 无工单），GUI 演示链路需可控草稿
- `docs/screenshots/`：列表页与详情页截图存档（README 引用）
- README：核心能力表 +长期记忆/HITL 两行、快速开始"6. 坐席工作台"（双终端启动 + 功能说明 + 截图 + demo 脚本）、API 表 +interventions 四接口、项目结构 +workbench/docs、测试数 269→346

**验证方式（真实 GUI 端到端，浏览器实测）**：
- `npm run build` 通过（TS 类型检查 + 3 路由）；`npm run dev` 启动 5173
- demo 后端造单（工单 #1/#2 欺诈拦截，interrupt 挂起）→ 浏览器验证：
  - 列表页：工单渲染 + 状态筛选 tabs + 计数 ✓
  - 详情页：REJECT 快照（风险分 100 + PROMISE/FRAUD_RISK×3 违规与建议）、轨迹（用户欺诈问题 + 安全话术 + single_domain/REJECT 徽章）、处理表单 ✓
  - **resolve 负路径**：坐席结论复述"代开发票/挂床"→ 复审 REJECT → 保守话术返回（"合规复核中"），工单已解决 + 会话回 active + 坐席回复落审计（3 条轨迹）——完整实证 F10 门禁对坐席文本同样生效
  - **resolve 正路径**：干净结论 → 复审 PASS → 结论原样返回用户（轨迹 PASS 徽章）
- 后端回归：ruff 全绿 + pytest 346 passed

**问题与修正**：
- RSC（服务端组件）fetch 相对路径 `/api/*` 报 "Failed to parse URL"——next rewrites 只作用于浏览器请求，服务端需绝对地址 → lib/api.ts 按 `typeof window` 分流
- Node REPL 里变量名 `agent` 与 browser runtime 全局冲突（TDZ 报错）→ 改名 agentBox
- 真实 LLM 拒答欺诈诱导（安全对齐）无法稳定触发 REJECT 造单 → demo 后端 mock 草稿（T037 恢复语义的确定性验证已由场景 11 单测覆盖，GUI 演示用 mock 链路）

**状态**：✅ 通过验证

**Git**：`feat: T038 Next.js 人工介入工作台（列表/详情可视化/resolve 恢复闭环 + README）`

### [T039] OTel + Jaeger 全链路追踪 — 2026-08-26

**操作**：
- 依赖（uv add）：opentelemetry-sdk/api 1.44.0 + instrumentation-fastapi + exporter-otlp-proto-grpc
- `services/observability/tracing.py`（新建）：setup_tracing（TracerProvider + Resource(claimflow) + OTLP gRPC exporter + ParentBased(TraceIdRatioBased) 采样 + FastAPIInstrumentor；幂等、开关关闭 no-op）+ traced_span 便捷上下文管理器（None 属性自动跳过）
- 埋点四接入（trace_id 贯穿 A06 → 节点 → LLM/工具）：
  - `app/main.py` 模块级 instrument_app（A06 server span 入口）
  - `llm_metrics.observed_ainvoke`：LLM span 一处埋点覆盖全部调用点，属性 gen_ai.request.model / claimflow.phase（token_tracker 新增 current_phase()）/ gen_ai.usage.input|output_tokens
  - `tools/executor.execute`：包装为公共入口 span（缓存命中/熔断拒绝也在 trace 内），属性 claimflow.tool.name
  - `nodes/compliance.py ComplianceNode`：compliance.review span 属性 verdict / risk_score
- 配置：otel_enabled（默认 false 零开销）/ otel_endpoint（默认 localhost:4317）/ otel_sampling_ratio，.env.example 同步
- `otelcol/config.yaml`（OTLP gRPC 4317 → otlphttp jaeger:4318）+ docker-compose.yml tracing profile：jaeger(all-in-one:1.71.0, UI 16686, COLLECTOR_OTLP_ENABLED) + otel-collector(0.123.0, 4317)
- `tests/observability/test_tracing.py`（新建 6 用例，InMemorySpanExporter + 测试 provider monkeypatch 不污染全局）：LLM span 属性（model/phase/tokens）/ 工具 span / 合规裁决属性 / 父子 span 共 trace_id / None 属性跳过 / 禁用 no-op
- `docs/screenshots/jaeger-trace-tree.png`：完整调用树截图存档

**验证方式（本地起栈真实链路）**：
- `docker compose --profile tracing up -d`：Jaeger UI 200 + Collector "Everything is ready"
- OTEL_ENABLED=true 起后端 + 真实 LLM 发"阑尾炎能赔多少"（multi_step 全链路）：Jaeger 单 trace **25 spans、唯一根为 A06 server span**——intent → planner → medical worker（record_query/diagnosis_matcher/claim_rule_rag）→ claim worker（policy_query/claim_rule_rag/claim_calculator）→ synthesize → compliance.review（verdict=PASS 属性），每个 LLM span 带分环节 token 用量
- `uv run python -m pytest -q` → 352 passed（原 346 + 新增 6）；ruff 全绿；验证完毕 `--profile tracing down` 清理

**问题与修正**：
- FastAPIInstrumentor 在 lifespan 内调用无效（Starlette middleware 栈在 lifespan 前已构建）→ server span 缺失，全部 LLM/工具 span 成独立 trace；移到模块级（app 创建后）修复，trace 链贯通
- otel/opentelemetry-collector 0.116.0 二进制在刚重启的 WSL2 引擎 exec 失败（"no such file or directory"，架构 amd64 正确——引擎冷启动偶发不兼容）→ 换 0.123.0 正常
- jaegertracing/all-in-one:1.62 tag 不存在 → 1.71.0
- OTel 1.44 的 InMemorySpanExporter 从 sdk.trace.export 移至 sdk.trace.export.in_memory_span_exporter 子模块

**状态**：✅ 通过验证

**Git**：`feat: T039 OTel + Jaeger 全链路追踪（LLM/工具/合规 span + tracing profile + 25-span 调用树）`

<!-- 遇到的问题记录在此，方便回溯 -->
| 编号 | 任务 | 问题 | 解决方案 | 状态 |
|------|------|------|---------|------|
| - | - | - | - | - |
