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

---

## 问题追踪

<!-- 遇到的问题记录在此，方便回溯 -->
| 编号 | 任务 | 问题 | 解决方案 | 状态 |
|------|------|------|---------|------|
| - | - | - | - | - |
