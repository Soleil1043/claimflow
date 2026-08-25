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

<!-- 遇到的问题记录在此，方便回溯 -->
| 编号 | 任务 | 问题 | 解决方案 | 状态 |
|------|------|------|---------|------|
| - | - | - | - | - |
