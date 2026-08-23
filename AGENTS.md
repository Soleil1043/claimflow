# claim-agent — AI 编程工具工作规范

> 本文件是 AI 编程工具的全局指令。Claude Code 读 `CLAUDE.md`，TRAE 读 `AGENTS.md`，Cursor 读 `.cursorrules`。
> 切换工具时将本文件内容复制到对应文件即可。

---

## 1. 项目是什么

**多智能体保险理赔对话系统** —— 用户咨询理赔问题时，系统自动调度多个专业 Agent（理赔核算 / 医疗审核 / 合规风控），通过工具调用完成跨系统查询，最终给出准确回答。

核心数据：

- 目标：复杂任务完成率从 58% → 75%，人工转接率从 62% → 37%
- 4 个 Agent：Orchestrator（调度）、Claim（理赔核算）、Medical（医疗审核）、Compliance（合规风控）
- 10+ 个工具：保单查询、理赔计算器、RAG 检索、诊断匹配、合规审查等

详细架构设计见 `docs/architecture.md`（架构文档不要放在这里，这里只给 AI 编程用）。

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
| 向量数据库     | Qdrant                            | 轻量单容器；开发期 local mode 零容器        |
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

- API 路由：RESTful 复数名词，如 `/api/v1/conversations`
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
claim-agent/
├── .agent/                    ← AI 工具状态（只追加，不删除）
│   ├── spec.md                ← 需求规格（Phase 1 产出）
│   ├── plan.md                ← 技术方案（Phase 2 产出）
│   ├── tasks.md               ← 任务清单（Phase 3 产出）
│   ├── progress.md            ← 构建日志（全程追加）
│   └── decisions.md           ← 决策记录（全程追加）
│
├── app/
│   ├── api/                   # FastAPI 路由
│   │   ├── v1/
│   │   │   ├── conversations.py
│   │   │   └── health.py
│   │   └── dependencies.py    # 依赖注入
│   ├── core/                  # 核心配置、日志、异常
│   │   ├── config.py
│   │   ├── logging.py
│   │   └── exceptions.py
│   └── main.py                # FastAPI 入口
│
├── agents/                    # Agent 定义
│   ├── orchestrator.py        # 调度 Agent
│   ├── claim.py               # 理赔核算 Agent
│   ├── medical.py             # 医疗审核 Agent
│   └── compliance.py          # 合规风控 Agent
│
├── nodes/                     # LangGraph 节点
│   ├── intent.py              # 意图识别节点
│   ├── planner.py             # 任务规划节点
│   ├── step_executor.py       # 步骤执行节点
│   ├── compliance.py          # 合规审查节点
│   ├── generator.py           # 回答生成节点
│   └── rag.py                 # RAG 检索节点
│
├── state.py                   # AgentState 定义
│
├── tools/                     # 工具层
│   ├── base.py                # BaseTool 基类
│   ├── registry.py            # 工具注册中心
│   ├── executor.py            # 工具执行器（超时/重试/熔断）
│   ├── claim/                 # 理赔类工具
│   │   ├── policy_query.py
│   │   ├── calculator.py
│   │   └── claim_rule_rag.py
│   ├── medical/               # 医疗类工具
│   │   ├── record_query.py
│   │   ├── diagnosis_matcher.py
│   │   └── ocr_extract.py
│   └── compliance/            # 合规类工具
│       ├── rule_check.py
│       ├── sensitive_filter.py
│       └── risk_scoring.py
│
├── services/                  # 服务层
│   ├── llm/                   # LLM 调用封装
│   │   ├── client.py
│   │   └── prompts.py
│   ├── rag/                   # RAG 服务
│   │   ├── embedder.py
│   │   ├── retriever.py
│   │   └── ingest.py
│   ├── memory/                # 记忆服务
│   │   ├── short_term.py
│   │   ├── working.py
│   │   └── long_term.py
│   ├── observability/         # 监控指标
│   │   └── metrics.py
│   └── db/                    # 数据库
│       ├── models.py
│       └── session.py
│
├── workflows/                 # LangGraph 工作流组装
│   └── main_graph.py          # 主图定义与编译
│
├── schemas/                   # Pydantic schema
│   ├── api.py                 # API 请求/响应
│   ├── agent.py               # Agent 相关类型
│   └── tools.py               # 工具输入输出类型
│
├── evals/                     # 评测脚本
│   ├── test_suite.py          # 测试集运行器
│   ├── metrics.py             # 评测指标计算
│   └── datasets/              # 标注测试用例
│
├── tests/                     # 单元测试
│   ├── tools/
│   ├── agents/
│   └── workflows/
│
├── grafana/                   # Grafana dashboard JSON
├── prometheus/                # Prometheus 配置
│
├── alembic/                   # 数据库迁移
│   ├── versions/
│   └── env.py

├── docs/
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

### 6.1 工具层约定

每个工具必须继承 `BaseTool`，实现以下接口：

```python
# 伪代码，实际以 base.py 为准
class BaseTool:
    name: str
    description: str  # 给 LLM 看的描述，要清晰说明什么时候用这个工具
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]

    async def execute(self, input_data: BaseModel) -> BaseModel:
        ...
```

- 工具执行器（`ToolExecutor`）统一处理：超时、重试、熔断、日志、指标上报
- 外部 API 调用通过 Adapter 模式封装，方便 mock 和替换
- 工具失败抛出 `ToolExecutionError`，由执行器统一处理

### 6.2 Agent 层约定

每个 Agent 有独立的：

- system prompt（存放在 `services/llm/prompts.py` 的模板变量中）
- 可用工具列表
- 输出格式约束（结构化输出）

Agent 不直接调工具，通过 LangGraph 的 `ToolNode` 或工具执行器调用。

### 6.3 工作流约定

- 主图定义在 `workflows/main_graph.py`，所有节点从 `nodes/` 导入
- 状态定义在 `state.py`，新增状态字段必须同步更新所有相关节点
- 所有输出路径必须经过 compliance 节点（用条件边保证）
- Checkpoint 用 PostgreSQLSaver，支持对话中断恢复
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

项目分 4 个 Phase，每个 Phase 完成后停下来等用户确认再进入下一个：

| Phase   | 目标                     | 产出                                 |
| ------- | ---------------------- | ---------------------------------- |
| Phase 0 | 项目脚手架 + 基础设施           | 项目能跑起来，有 health check              |
| Phase 1 | MVP：单 Agent ReAct 核心流程 | 能对话，能调用 2-3 个工具回答问题                |
| Phase 2 | 多智能体协作                 | Orchestrator + 3 个 Worker Agent 协作 |
| Phase 3 | 工程化与优化                 | 容错、监控、评测、性能优化                      |
| Phase 4 | 深度亮点                   | GraphRAG、A/B 测试、高级特性               |

具体任务清单见 `.agent/tasks.md`（在 Phase 规划阶段生成）。

---

## 8. 注意事项

1. **外部服务用 Mock 起步**：保单 API、医疗 API 等第三方系统，先用 Mock 实现跑通流程，后面再替换真实接口。Mock 数据要真实可信（参考真实理赔场景）。
2. **RAG 用样本文档**：理赔规则知识库先准备 10-20 篇真实感的 markdown 文档（保险条款、理赔规则、免责说明等），用这些数据演示 RAG 效果。
3. **先跑通再优化**：每个功能先做最简可运行版本，验证逻辑正确后再加优化（缓存、并发、性能调优等）。
4. **遇到不确定的先问**：业务逻辑、技术选型有疑问时，不要自己猜，写到 `decisions.md` 并提示用户确认。
5. **中文注释**：代码注释和文档用中文，和项目语境保持一致。
