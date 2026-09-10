# claimflow — AI 编程工具工作规范

> 本文件是 AI 编程工具的全局指令。Claude Code 读 `CLAUDE.md`，TRAE 读 `AGENTS.md`，Cursor 读 `.cursorrules`。
> 切换工具时将本文件内容复制到对应文件即可。

---

## 1. 项目是什么

**保险理赔智能核赔平台（多险种）** —— 客户提交理赔申请与材料后，LLM Orchestrator 动态调度专业 Worker（材料审核 / 保单核验 / 风控筛查 / 责任认定 / 金额理算 / 决定书生成）完成自动核赔：低风险小额案件自动签发《理赔决定书》，其余转人工复核。领域知识以 skill 作业规程包（`skills/<stage>/<line>.md`）承载。

核心要点：

- LLM Orchestrator-Worker 架构（D039）：结构化路由决策 + 前置条件守卫（代码层）+ **静态合规门**（图结构保证不可绕过）+ 失败兜底默认计划
- skill 机制：每阶段×险种一份 SKILL.md 作业规程，准确率迭代改文本不改代码
- 多险种：case_type 枚举（medical/auto/property/accident），worker 按"险种 pack"分批上线——首批医疗险，未上线险种受理期转人工
- 分级自动：阈值全部配置化（pydantic-settings）

决策链见 `.agent/decisions.md`（D037-D039 为核赔平台方向决策）。

总体架构与实施依据：`docs/claimflow-新架构设计-v2.md`（工程版）+ 同目录人话版导读。
任务清单：`.agent/tasks.md` Phase 8（T077-T093）。

---

## 2. 工作流约束（强制）

1. **先读状态再动手**：每次新对话，先读取 `.agent/` 目录下所有 `.md` 文件，搞清楚当前进度再开始。
2. **按任务清单走**：严格按照 `.agent/tasks.md` 的顺序执行，不跳依赖，不同时做多个任务。
3. **完成一个记一个**：每完成一个任务必须做 4 件事：
   - 在 `tasks.md` 把那项标成 `[x]`
   - 在 `progress.md` 追加一行记录（做了什么、关键改动、结果）
   - 单独 git commit
   - 停下来等用户确认，再做下一个
4. **技术选型先记录再实现**：遇到需要决策的技术选型，先写进 `.agent/decisions.md`（选项 + 理由 + 最终选择），再继续。
5. **不超前实现**：只做当前任务范围内的事，不"顺便"做后面的功能。
6. **历史记录只追加不删**：`progress.md` 和 `decisions.md` 永远只追加，不修改历史。

---

## 3. 技术栈

| 类别        | 选型                                | 说明                                     |
| --------- | --------------------------------- | -------------------------------------- |
| 语言        | Python 3.12                       | 必须用类型注解                                |
| Agent 框架  | LangGraph                         | 状态机 + Checkpoint                       |
| Web 框架    | FastAPI                           | async 风格                               |
| 关系数据库     | PostgreSQL + SQLAlchemy 2.0 async | LangGraph Checkpoint 用 PostgreSQLSaver |
| 向量数据库     | Qdrant                            | 轻量单容器；开发期 local mode 零容器               |
| 缓存        | Redis                             | 会话缓存 + 工具结果缓存                          |
| LLM       | OpenAI 兼容接口                       | 通过配置切换模型                               |
| Embedding | BGE-M3                            | 本地部署或 API                              |
| 包管理       | uv                                | `pyproject.toml` + `uv.lock`           |
| 测试        | pytest + pytest-asyncio           | 核心逻辑必须有测试                              |
| 部署        | Docker + Docker Compose           | 本地一键启动                                 |
| 监控        | Prometheus + Grafana              | Phase 3 实现                             |

---

## 4. 代码约定

### 4.1 通用

- 所有函数加类型注解，禁止 `Any` 满天飞（实在不确定的地方用 `typing.Any` 并加注释说明）
- 配置统一通过 `pydantic-settings` 从环境变量读取，不硬编码
- 每个模块单一职责，文件超过 300 行考虑拆分
- 错误处理只在系统边界（用户输入、外部 API 调用），内部代码信任调用方传参正确
- 日志用 `structlog` 结构化日志，不 print

### 4.2 命名

- API 路由：RESTful 复数名词，如 `/api/v1/cases`
- 变量/函数：snake_case
- 类名：PascalCase
- 常量：UPPER_SNAKE_CASE
- 私有成员：前缀 `_`

### 4.3 测试

- 单元测试放在 `tests/`，目录结构和源码对应
- 测试文件命名：`test_<模块名>.py`
- 核心业务逻辑（工具执行、状态流转、容错机制）必须有单元测试
- 外部 API 调用用 mock，不测第三方服务

### 4.4 Git

- 每个任务一个 commit：`feat: T0XX [任务简述]`
- 修复 commit：`fix: T0XX [问题描述]`
- 文档 commit：`docs: [内容]`
- 重构 commit：`refactor: [内容]`
- 不用 `--no-verify`，不用 `git add -A`，按文件添加

---

## 5. 项目结构

```
claimflow/
├── .agent/                    ← AI 工具状态（只追加，不删除）
│   ├── spec.md                ← 需求规格
│   ├── plan.md                ← 技术方案
│   ├── tasks.md               ← 任务清单
│   ├── prompts.md             ← Prompt 备忘
│   ├── progress.md            ← 构建日志（全程追加）
│   └── decisions.md           ← 决策记录（全程追加）
│
├── app/
│   ├── api/
│   │   ├── dependencies.py    # 依赖注入
│   │   └── v1/
│   │       ├── cases.py           # 核赔案件：提交 / 详情 / 材料上传
│   │       ├── interventions.py   # HITL 核赔工单
│   │       └── health.py
│   ├── core/                  # 配置、日志、异常、事件循环
│   │   ├── config.py
│   │   ├── eventloop.py
│   │   ├── logging.py
│   │   └── exceptions.py
│   └── main.py                # FastAPI 入口
│
├── nodes/                     # 核赔主图节点
│   ├── intake.py              # 受理论证（险种分类 + 未上线转人工）
│   ├── orchestrator.py        # LLM 调度（RoutingDecision + Send 并行 + 守卫 + 兜底）
│   ├── material_review.py     # 材料审核（提取 / 完整性 / AI 一致性）
│   ├── policy_verify.py       # 保单核验
│   ├── fraud_check.py         # 风控筛查
│   ├── liability_judge.py     # 责任认定（确定性前置 + ReAct + 关键词兜底）
│   ├── amount_calc.py         # 金额理算（纯确定性）
│   ├── decision_generate.py   # 决定书生成（骨架渲染 + LLM 叙述）
│   ├── compliance_gate.py     # 静态合规门（金额断言 + 红线 + 三态）
│   └── human_gate.py          # 人工介入门（interrupt 挂起）
│
├── state.py                   # ClaimCaseState 主图共享状态
│
├── tools/                     # 工具层
│   ├── base.py                # ClaimflowTool 基类（继承 langchain 官方 BaseTool）
│   ├── guards.py              # 守卫：熔断/缓存/超时（GuardedTool，D021 自研保留项）
│   ├── factory.py             # 工厂装配（.with_retry + 守卫）
│   ├── claim/                 # 理赔类工具（policy_query / calculator / claim_rule_rag）
│   ├── medical/               # 医疗类工具（record_query / diagnosis_matcher / ocr_extract）
│   ├── compliance/            # 合规类工具（rule_check / sensitive_filter / risk_scoring）
│   ├── fraud/                 # 风控工具（blacklist / history / rules）
│   └── document/              # 文档规则工具（classify / completeness）
│
├── services/                  # 服务层
│   ├── case_service.py        # 案件领域服务（幂等 / 工单投影 / 决定书视图）
│   ├── case_jobs.py           # 案件交付队列（任务表 + 常驻单消费者）
│   ├── case_store.py          # 案件 / 审计事件落库
│   ├── amounts.py             # 金额理算（规格公式，Decimal）
│   ├── decision_doc.py        # 决定书渲染与红线检查
│   ├── skills.py              # skill 作业规程装载
│   ├── materials.py           # 材料提取（图片/PDF/Word，T049）
│   ├── worker_agent.py        # Worker 子图装配与执行（create_agent）
│   ├── cache.py               # 工具结果缓存
│   ├── llm/                   # LLM 封装（client / prompts）
│   ├── rag/                   # RAG（embedder / retriever / reranker / knowledge_graph / graph_retriever / qdrant_client / ingest）
│   ├── memory/                # 记忆（short_term / long_term / case_memory）
│   ├── observability/         # 可观测性（metrics / llm_metrics / token_tracker / tracing）
│   └── db/                    # 数据库（models / session）
│
├── workflows/
│   └── case_graph.py          # 核赔主图定义与编译
│
├── schemas/                   # Pydantic schema
│   ├── api.py                 # API 请求/响应
│   ├── case.py  stages.py     # 案件模型 / 阶段产出模型
│   ├── contract.py            # 规格契约（阈值 / 金额公式单源）
│   ├── lines.py               # 险种 pack
│   ├── agent.py  tools.py     # Agent 类型 / 工具入参出参
│
├── evals/                     # 金样本评测门
│   ├── adjudication_suite.py  # 六门评测运行器（确定性 / --llm 两模式）
│   ├── adjudication_metrics.py  gates.py  judge.py  metrics.py  trajectory.py  schemas.py
│   └── datasets/adjudication.json
│
├── skills/                    # 作业规程包（<stage>/<line>.md，准确率迭代改文本不改代码）
│
├── tests/                     # 单元测试（目录结构与源码对应）
│
├── chatui/                    # 案件提交门户（Next.js 15 + Tailwind 4，端口 3000）
├── workbench/                 # 坐席工作台（Next.js，工单列表 / 详情 / 签批改判）
├── scripts/                   # 种子数据 / verify 验证脚本 / 知识图谱构建
├── data/                      # 运行数据：mock 种子 / kb_docs 知识库 / graph / qdrant 本地存储
│
├── grafana/                   # Grafana dashboard JSON
├── prometheus/                # Prometheus 配置
├── otelcol/                   # OTel Collector 配置（T039）
│
├── alembic/                   # 数据库迁移
│   ├── versions/
│   └── env.py
│
├── docs/                      # 设计文档（claimflow-新架构设计-v2.md 工程版 + 人话版导读）
│
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── pyproject.toml
├── uv.lock
└── AGENTS.md                  ← 本文件
```

---

## 6. 实现约定

### 6.1 工具层约定（T044 起对齐 LangChain 官方基类）

每个工具继承 langchain 官方基类（项目基类 `ClaimflowTool`，见 `tools/base.py`）：

```python
# 伪代码，实际以 base.py 为准
class MyTool(ClaimflowTool):            # 继承 langchain_core.tools.BaseTool
    name: str
    description: str                    # 给 LLM 看的描述，要清晰说明什么时候用这个工具
    args_schema: type[BaseModel]        # 入参校验（框架自动）+ bind_tools 自动生成 schema

    async def _arun(self, **kwargs) -> dict: ...
```

- 业务失败作为正常返回内容（dict 含 `success=False` / `error_message` 键），系统异常向上抛
- 守卫由 `tools/factory.py` 统一装配：官方 `.with_retry()` 重试 + `GuardedTool`（`tools/guards.py`：
  超时 / 熔断 / 缓存白名单——熔断与缓存为 D021 仅有的自研保留项）
- 外部 API 调用通过 Adapter 模式封装，方便 mock 和替换

### 6.2 Agent 层约定

每个 Agent 有独立的：

- system prompt（存放在 `services/llm/prompts.py` 的模板变量中）
- 可用工具列表
- 输出格式约束（结构化输出）

Agent 不直接调工具，通过 Worker 子图（`services/worker_agent.py`）或 `ToolNode` 调用。

### 6.3 工作流约定

- 主图定义在 `workflows/case_graph.py`，所有节点从 `nodes/` 导入
- 状态定义在 `state.py`，新增状态字段必须同步更新所有相关节点
- 决定书生成 → 合规门（compliance_gate）是图结构静态边，任何路由决策不可绕过
- Checkpoint：prod 用 AsyncPostgresSaver、dev 用 InMemorySaver，支撑 interrupt 挂起与跨重启恢复
- 图的入口和出口用 `__start__` / `__end__`

### 6.4 Prompt 约定

- 所有 Prompt 集中放在 `services/llm/prompts.py`，用字符串常量或 Jinja2 模板
- Prompt 中需要变量时用 `{variable}` 占位，调用时 format
- 结构化输出的 Prompt 必须包含输出格式说明和示例
- Prompt 文件不写业务逻辑，只存模板文本

### 6.5 配置约定

- 所有配置项在 `app/core/config.py` 中用 `pydantic-settings` 定义
- 敏感信息（API Key、密码等）只从环境变量读取
- `.env.example` 列出所有需要的环境变量，不含真实值
- 配置项有默认值的给合理默认，没有的设为必填

---

## 7. Phase 推进方式

项目按 Phase 推进，每个 Phase 完成后停下来等用户确认再进入下一个：

| Phase   | 目标                     | 产出                                 |
| ------- | ---------------------- | ---------------------------------- |
| Phase 0 | 项目脚手架 + 基础设施           | 项目能跑起来，有 health check              |
| Phase 1 | MVP：单 Agent ReAct 核心流程 | 能对话，能调用 2-3 个工具回答问题                |
| Phase 2 | 多智能体协作                 | Orchestrator + 3 个 Worker Agent 协作 |
| Phase 3 | 工程化与优化                 | 容错、监控、评测、性能优化                      |
| Phase 4 | 深度亮点                   | GraphRAG、A/B 测试、高级特性               |
| Phase 8 | 核赔平台重写（D037-D039）     | 案件驱动核赔管线 + 金样本评测门（T077-T093）       |

上列 Phase 均已交付。当前为维护期：新需求先写入 `.agent/tasks.md` 再实施，
历史决策链见 `.agent/decisions.md`。

---

## 8. 注意事项

1. **外部服务用 Mock 起步**：保单 API、医疗 API 等第三方系统，先用 Mock 实现跑通流程，后面再替换真实接口。Mock 数据要真实可信（参考真实理赔场景）。
2. **RAG 用样本文档**：理赔规则知识库先准备 10-20 篇真实感的 markdown 文档（保险条款、理赔规则、免责说明等），用这些数据演示 RAG 效果。
3. **先跑通再优化**：每个功能先做最简可运行版本，验证逻辑正确后再加优化（缓存、并发、性能调优等）。
4. **遇到不确定的先问**：业务逻辑、技术选型有疑问时，不要自己猜，写到 `decisions.md` 并提示用户确认。
5. **中文注释**：代码注释和文档用中文，和项目语境保持一致。
