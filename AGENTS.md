# claimflow — Agent 工作规则

> 本文件是 Agent 的全局指令。不同 Harness 会自动读取项目根目录的规则文件：
> TRAE / Claude Code → `AGENTS.md` / `CLAUDE.md` | Cursor → `.cursor/rules/*.mdc` | Windsurf → `.windsurfrules` | Cline → `.clinerules`
> 如果切换 Harness，将本文件内容复制到对应文件即可。

**标准开发流程**：需求分析 → 方案设计 → 构建 → 测试 → 上线。前两个阶段为对话式推进（产物：`.agent/spec.md` 需求规格说明书、`.agent/plan.md` 设计说明书），阶段结束由用户主动宣布；本文件约束构建及之后的执行。

**项目**：保险理赔智能核赔平台（多险种）——客户提交理赔申请与材料后，LLM Orchestrator 动态调度专业 Worker（材料审核 / 保单核验 / 风控筛查 / 责任认定 / 金额理算 / 决定书生成）完成自动核赔：低风险小额案件自动签发《理赔决定书》，其余转人工复核。领域知识以 skill 作业规程包（`skills/<stage>/<line>.md`）承载。

架构文档：`docs/architecture.md`（现状总览，16 节）+ `docs/claimflow-新架构设计.md`（工程版设计依据）+ 同目录人话版导读。统一语言：`CONTEXT.md`。决策链：`.agent/decisions.md`（D001-D076）。**现状为维护期**，新需求先写入 `.agent/tasks.md` 再实施。

---

## 1. 工作流约束（强制）

1. **启动时恢复上下文**：每次对话开始，先读取 `.agent/` 目录下所有 `.md` 文件，理解当前项目状态
2. **按清单执行**：严格按 `.agent/tasks.md` 中的任务顺序执行，不跳依赖，不一次做多个任务
3. **完成即记录**：每完成一个任务，必须执行以下 4 步：
   - 在 `tasks.md` 中将对应项标记为 `[x]`
   - 在 `progress.md` 中追加构建记录
   - 单独 git commit
   - 提示用户验证，等待确认后再做下一个
4. **决策留痕**：遇到需要决策的技术选型，先写入 `decisions.md`（说明选项和理由），再继续
5. **不超前实现**：只实现当前任务，不做"顺便"的额外功能
6. **不删不改状态文件的历史记录**：`progress.md` 和 `decisions.md` 只追加不删除
7. **需求变更走修订**：构建中要改功能，先用 `prompts.md` 的「需求变更 Prompt」修订 `spec.md` 并记录变更，同步 plan/tasks 后才改代码，不直接改实现
8. **设计变动先算账**：任何架构/设计变动（技术栈、数据模型、模块结构、关键接口、第三方依赖），先在对话中分析**优势与代价**（收益、风险、影响范围、迁移成本），经用户主动确认后才执行；同步修订 `plan.md`、在 `decisions.md` 记录、回退受影响任务，不直接改实现（见 `prompts.md` 的「设计变更 Prompt」）

---

## 2. 技术栈

| 类别 | 选型 | 说明 |
|---|---|---|
| 语言 | Python 3.12 | 必须用类型注解 |
| Agent 框架 | LangGraph | 状态机 + Checkpoint |
| Web 框架 | FastAPI | async 风格 |
| 关系数据库 | PostgreSQL + SQLAlchemy 2.0 async | LangGraph Checkpoint 用 PostgreSQLSaver |
| 向量数据库 | Qdrant | 轻量单容器；开发期local mode 零容器 |
| 缓存 | Redis | 会话缓存 + 工具结果缓存 |
| LLM | OpenAI 兼容接口 | 配置切换模型（当前 deepseek-flash，D056/T130，原生多模态兼 vision） |
| Embedding | BGE-M3 | 本地部署或 API |
| 包管理 | uv | `pyproject.toml` + `uv.lock` |
| 测试 | pytest + pytest-asyncio | 核心逻辑必须有测试 |
| 部署 | Docker + Docker Compose | 本地一键启动 |
| 监控 | Prometheus + Grafana + OTel/Jaeger | 14 面板 + trace_id 全链路 |

---

## 3. 代码约定

### 3.1 通用

- 所有函数加类型注解，禁止 `Any` 满天飞（实在不确定的地方用 `typing.Any` 并加注释说明）
- 配置统一通过 `pydantic-settings` 从环境变量读取，不硬编码；敏感信息只从环境变量读
- 每个模块单一职责，**逻辑模块**超过 300 行考虑拆分（T153 口径：纯模型/数据定义文件豁免——`schemas/api.py`、`services/db/models.py` 等字段声明无逻辑可拆；路由/服务文件略超线且边界模糊时，拆分收益低于 churn 风险的可记录豁免理由后保留）
- 错误处理只在系统边界（用户输入、外部 API 调用），内部代码信任调用方传参正确
- 日志用 `structlog` 结构化日志，不 print；**代码注释与文档用中文**
- `.env.example` 列出所有环境变量，不含真实值

### 3.2 命名

- API 路由：RESTful 复数名词，如 `/api/v1/cases`
- 变量/函数：snake_case | 类名：PascalCase | 常量：UPPER_SNAKE_CASE | 私有成员：前缀 `_`

### 3.3 测试

- 单元测试放在 `tests/`，目录结构和源码对应；测试文件命名 `test_<模块名>.py`
- 核心业务逻辑（工具执行、状态流转、容错机制）必须有单元测试
- 外部 API 调用用 mock，不测第三方服务
- 当前全量 **528 passed**

### 3.4 工具层（T044 起对齐 LangChain 官方基类）

每个工具继承项目基类 `ClaimflowTool`（`tools/base.py`，底层为 `langchain_core.tools.BaseTool`）：

```python
class MyTool(ClaimflowTool):
    name: str
    description: str                # 给 LLM 看，要清晰说明何时用
    args_schema: type[BaseModel]    # 入参校验（框架自动）+ bind_tools 自动生成 schema

    async def _arun(self, **kwargs) -> dict: ...
```

- 业务失败作为正常返回内容（dict 含 `success=False` / `error_message`），系统异常向上抛
- 守卫由 `tools/factory.py` 统一装配：官方 `.with_retry()` 重试 + `GuardedTool`（`tools/guards.py`：超时/ 熔断 / 缓存白名单——熔断与缓存为 D021 仅有的自研保留项）
- 外部 API 通过 Adapter 模式封装，方便 mock 和替换

### 3.5 工作流

- 主图定义在 `workflows/case_graph.py`，节点从 `nodes/` 导入；状态定义在 `state.py`
- 新增状态字段必须同步更新所有相关节点，**并递增 `CASE_SCHEMA_VERSION`**（T141 checkpoint 版本门卫）
- 决定书生成 → 合规门是图结构静态边，任何路由决策不可绕过
- Checkpoint：prod 用 AsyncPostgresSaver、dev 用 InMemorySaver，`thread_id = case_id`
- 恢复一律经 `Command(resume=...)`；挂起工单信息以**交付回执**为单源派生（D047），禁止读 checkpoint 猜状态
- 阶段知识（名字/ 前置 / 快照字段 / 回边）查 `schemas/stages.py` StageSpec 注册表，节点内不复制阶段清单
- 图的入口和出口用 `__start__` / `__end__`

### 3.6 Prompt（T165硬约束）

- 所有 Prompt 集中在 `services/llm/prompts.py`，只存模板文本不写业务逻辑
- **system 必须全静态逐字稳定**——同一 stage×line 内每次调用内容完全一致，才能整段命中
  DeepSeek 前缀缓存。案件快照 / 材料提取结果 / 理算事实等每案唯一的数据一律走
  **user message** 并用 `<<<DATA…>>>DATA` 定界
- 动态占位符插进 system 会让前缀分叉点落在唯一数据开头，命中率归零
  （实测 orchestrator 改造前 0% → 改造后 86.8%）
- skill 规程文本可含任意大括号，装配顺序为「先 format base prompt → 再拼 skill」，
  但 base prompt 内**不得**填动态数据

### 3.7 配置

- 所有配置项在 `app/core/config.py` 中用 `pydantic-settings` 定义
- 配置项有默认值的给合理默认，没有的设为必填

---

## 4. Git 规范

- 每个任务一个 commit：`feat: T0XX [任务简述]`
- 修复 commit：`fix: T0XX [问题描述]`
- 文档 commit：`docs: [内容]`
- 重构 commit：`refactor: [内容]`
- 不使用 `--no-verify` 跳过 hook
- 不使用 `git add -A`，按文件添加

---

## 5. 文件结构约定

```
claimflow/
├── .agent/                    ← 项目状态（Agent 读写，不删除）
│   ├── spec.md                ← Phase 1: 需求规格说明书（9 节，含Fxx/Exx）
│   ├── plan.md                ← Phase 2: 设计说明书
│   ├── tasks.md               ← Phase 3: 任务清单（T001-T168）
│   ├── progress.md            ← Phase 4: 构建日志（全程追加）
│   ├── decisions.md           ← 全程: 决策记录（D001-D076）
│   └── prompts.md             ← Prompt 模板参考（需求变更/ 设计变更/ 回退/ 上下文恢复）
├── CONTEXT.md                 ← 领域术语表（统一语言）
├── state.py                   # ClaimCaseState 主图共享状态
│
├── app/                       # FastAPI 入口与 API 层（只做 HTTP 边界）
│   ├── api/v1/                # cases / interventions / support / memory / health
│   ├── core/                  # config / logging / eventloop / exceptions
│   └── main.py
│
├── nodes/                     # 核赔主图节点（只做编排与状态流转）
│   ├── intake.py              # 受理论证（险种分类 + 未上线转人工）
│   ├── orchestrator.py        # LLM 调度（RoutingDecision + Send 并行 + 审计）
│   ├── guards.py              # 确定性守卫纯函数层（T118）
│   ├── material_review.py     # 材料审核（@task并行提取 / 完整性 / AI 一致性）
│   ├── policy_verify.py       # 保单核验
│   ├── fraud_check.py         # 风控筛查
│   ├── liability_judge.py     # 责任认定（确定性前置 + ReAct + 关键词兜底）
│   ├── amount_calc.py         # 金额理算（纯确定性）
│   ├── decision_generate.py   # 决定书生成（骨架渲染 + LLM 叙述）
│   ├── compliance_gate.py     # 静态合规门（金额断言 + 红线 + 三态）
│   ├── auto_adjudicate.py     # 分级自动签发 + 叙述 5% 采样（T139）
│   └── human_gate.py          # 人工介入门（interrupt 挂起 + 三类工单决议）
│
├── tools/                     # 工具层（ClaimflowTool 基类 + 熔断/缓存/超时守卫）
│   ├── claim/ medical/ compliance/ fraud/ document/ support/
│   └── base.py guards.py factory.py
│
├── services/                  # 服务层（领域服务与基础设施封装）
│   ├── case_service.py        # 案件领域服务（幂等 / 工单投影 / 决定书视图 / resume 载荷单源）
│   ├── case_jobs.py           # 交付队列生产半区（T153）
│   ├── case_job_worker.py     # 交付队列消费半区（CAS 租约认领多实例 T155）
│   ├── case_resume_guard.py   # checkpoint 版本门卫（T141/T155）
│   ├── case_store.py          # 案件 / 审计事件落库
│   ├── amounts.py             # 金额理算（规格公式，Decimal）
│   ├── decision_doc.py        # 决定书渲染与红线检查
│   ├── skills.py              # skill 作业规程装载 + build_system_prompt（T165 布局约束）
│   ├── materials.py           # 材料提取（图片/PDF/Word 两段式）
│   ├── worker_agent.py        # Worker 子图装配与执行（create_agent + 官方中间件栈）
│   ├── support/               # 在线客服域（store 状态机 / agent 装配）
│   ├── cache.py               # 工具结果缓存
│   ├── llm/                   # LLM 封装（client / prompts）
│   ├── rag/                   # RAG（embedder / retriever / reranker / knowledge_graph / qdrant / ingest）
│   ├── memory/                # 记忆（short_term / long_term / case_memory 申请人记忆治理）
│   ├── observability/         # 可观测性（metrics / llm_metrics / token_tracker / tracing）
│   └── db/                    # 数据库（models 11 表 / session 双后端引擎）
│
├── workflows/case_graph.py    # 核赔主图定义与编译
│
├── schemas/                   # Pydantic schema（api / case / stages / contract / lines / tools）
├── evals/                     # 金样本评测门（suite / gates / metrics / redteam / rag_metrics / single_agent_baseline）
├── skills/                    # 作业规程包（<stage>/<line>.md，四险种 21 份；改文本不改代码）
├── tests/                     # 单元测试（目录结构与源码对应，528 用例）
│
├── chatui/                    # 案件提交门户（Next.js 15 + Tailwind 4，端口 3000；含悬浮 AI 客服）
├── workbench/                 # 坐席工作台（Next.js：核赔工单 / 客服工单 / 叙述抽评审）
├── scripts/                   # 种子 / 生成 / 冒烟双档 / 红队 / 缓存诊断
├── data/                      # 运行数据：mock 种子 / kb_docs 知识库 / graph / qdrant / uploads
│
├── grafana/                   # Grafana dashboard JSON（双仪表盘 14 面板）
├── prometheus/                # Prometheus 抓取配置（retention 30d）
├── otelcol/                   # OTel Collector 配置（采样率 0.2）
│
├── alembic/                   # 数据库迁移
├── docs/                      # architecture.md（现状总览）+ 新架构设计 + exercises + diagrams
├── .github/workflows/ci.yml   # CI：ruff + pytest + 评测门 + docker 冒烟双档 + 双副本多实例冒烟
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── pyproject.toml
├── uv.lock
└── AGENTS.md                  ← 本文件
```

---

## 6. 注意事项

1. **外部服务用 Mock 起步**：保单 API、医疗 API 等第三方系统先用 Mock 跑通流程，
   Mock 数据要真实可信（参考真实理赔场景）
2. **RAG 用样本文档**：理赔规则知识库准备真实感的 markdown 文档（保险条款、理赔规则、
   免责说明等）演示 RAG 效果
3. **先跑通再优化**：每个功能先做最简可运行版本，验证逻辑正确后再加优化
4. **遇到不确定的先问**：业务逻辑、技术选型有疑问时不要自己猜，写到 `decisions.md`
   并提示用户确认
5. **dev 多实例须共享 checkpoint**：交付队列已多实例安全（T155 租约认领，D071），
   但 dev 默认 checkpointer 是进程内存——多实例（多端口/多进程共享同一 data 目录）
   必须显式设 `CHECKPOINT_BACKEND=sqlite`，否则他实例认领 RESUME 后找不到
   checkpoint 会触发版本门卫全新重跑（T145 实锤）。本地复现双实例：
   `uv run python -m scripts.verify_multiinstance --boot --offline`（自起 8010/8011）；
   prod 多副本冒烟在 CI docker job 以 `docker-compose.replicas.yml --scale app=2` 执行
6. **改动守评测门**：任何涉及调度 / 理算 / 决定书的改动，合并前必跑
   `evals/adjudication_suite`（主门 + 对抗门）与 `uv run pytest -q`，六门不退化才算过
7. **容器重建才生效**：加指标或改 `.env` 后必须 `docker compose build app` /
   `--force-recreate`，只 `up -d` / `restart` 容器仍跑旧代码
