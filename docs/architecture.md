# 多智能体保险理赔助手 — Agent 架构设计文档

> **文档状态**：v1 设计于 2026-08-24（规划期）；**v2 全面重构已于 2026-09-02 实施完成**
> （T044-T048，D021-D023/ADR-007）——LangGraph/LangChain 标准构件对齐：supervisor 动态路由、
> create_agent Worker/React 子图、with_structured_output 枚举判决、官方工具基类 + 守卫层、
> 官方 Store 长期记忆。本文档即现行架构；v1 设计原貌由 Git 历史承担。
> 演进决策全链见 ADR-001~007 与 `.agent/decisions.md`（D001-D025）。

## 1. 项目概述

### 1.1 背景与问题

保险理赔场景中，用户一次咨询往往涉及多个系统的信息联动：
- 需要查询保单信息（保单系统 API）
- 需要核对医疗数据（医院数据接口）
- 需要匹配理赔规则（规则引擎 / 知识库）
- 需要合规审查（风控合规系统）

传统客服机器人只能处理 FAQ 类单轮问答，面对需要多步推理、跨系统查询的复杂诉求（"我这个情况能赔多少？需要什么材料？"）无法处理，导致**人工转接率高达 62%**，平均处理时长 15 分钟。

### 1.2 目标

构建一个多智能体协作的理赔对话系统，实现：
- 复杂理赔咨询的端到端自动处理
- 多工具动态调用与结果整合
- 合规风险自动识别与拦截
- 人工转接前的信息预收集，提升人工处理效率

### 1.3 成功指标

| 指标 | 基线 | 目标 |
|---|---|---|
| 复杂任务完成率 | 58% | ≥ 75% |
| 人工转接率 | 62% | ≤ 37% |
| 平均处理时长 | 15 min | ≤ 4 min |
| 工具调用准确率 | - | ≥ 92% |
| 合规违规率 | - | 降低 65% |
| Token 成本 | - | 优化 28% |

---

## 2. 总体架构

### 2.1 架构选型：Orchestrator-Worker 模式

**为什么选 Orchestrator-Worker 而不是其他模式？**

| 模式 | 优点 | 缺点 | 适用场景 |
|---|---|---|---|
| ReAct 单 Agent | 简单灵活，适合动态探索 | 复杂任务容易跑偏，角色不清晰 | 工具少、流程不确定的场景 |
| Plan-Execute | 先规划再执行，逻辑性强 | 遇到异常难调整，灵活性差 | 步骤明确的结构化任务 |
| **Orchestrator-Worker** | 角色分工明确，各 Agent 专精领域，调度层统一协调 | 架构复杂，需要设计 Agent 间通信协议 | **多领域、多步骤、有明确角色分工的复杂任务** |

理赔场景有三个特点决定了选 Orchestrator-Worker：
1. **领域边界清晰**：保单查询、医疗审核、合规风控是三个独立领域
2. **专业能力差异大**：每个领域需要的工具和推理逻辑不同
3. **风控要求高**：合规审查需要独立角色，避免利益冲突

> **v2 落地方式**：Orchestrator-Worker 在 LangGraph 中的标准表达即 **Supervisor 多智能体模式**
> （官方 multi-agent 文档范式）：supervisor 节点每轮结构化输出下一步动作，经 `Command(goto=...)`
> 路由到 Worker 子图，FINISH 后整合。v1 的"planner 一次性计划 + 游标循环"是 Plan-Execute 变体，
> v2 统一收敛到 supervisor（计划感知，支持执行中重规划）。

### 2.2 架构图

```
                        ┌─────────────────────┐
                        │   用户对话入口       │
                        │  (FastAPI + 前端)    │
                        └─────────┬───────────┘
                                  │
                                  ▼
                        ┌─────────────────────┐
                        │  Orchestrator Agent │
                        │  (调度 & 决策中心)   │
                        └────┬──────┬──────┬──┘
                             │      │      │
              ┌──────────────┘      │      └──────────────┐
              ▼                     ▼                     ▼
   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
   │  理赔核算 Agent  │  │  医疗审核 Agent  │  │  合规风控 Agent  │
   │  (Claim Agent)   │  │  (Medical Agent) │  │  (Compliance)    │
   └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
            │                     │                     │
            ▼                     ▼                     ▼
   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
   │ 保单查询 API     │  │ 医疗数据接口     │  │ 合规规则库       │
   │ 保费计算器       │  │ 诊断编码匹配     │  │ 风险评分引擎     │
   │ 理赔规则 RAG     │  │ 病历 OCR 解析    │  │ 敏感词过滤       │
   └──────────────────┘  └──────────────────┘  └──────────────────┘
```

### 2.3 协作流程

```
用户提问
   │
   ▼
Orchestrator 理解意图 → 判断任务类型
   │
   ├─ 简单 FAQ → 直接回答（走理赔规则 RAG）
   │
   ├─ 单领域查询 → 指派对应 Worker Agent
   │    │
   │    └─ Worker Agent 调用工具 → 返回结果
   │
   └─ 复杂多步任务 → 制定执行计划，依次调度
        │
        ├─ Step 1: 医疗审核 Agent 核对诊断与材料
        ├─ Step 2: 理赔核算 Agent 计算赔付金额
        └─ Step 3: 合规风控 Agent 审查结果
              │
              ▼
        Orchestrator 整合所有结果 → 生成最终回答
              │
              ▼
        合规不通过 → 触发人工复核 / 拒绝回答
```

### 2.4 v2 总体原则：标准构件优先（D021）

v2 以「LangGraph 图结构为骨架」重构：**主图 = StateGraph，一切模块皆为图上标准构件**。
凡 LangGraph/LangChain 已定义的类、方法、架构一律使用；自研仅保留框架确实没有对应物的最小集合。

**模块 → 官方构件映射**（v1 → v2，已于 2026-09-02 T044-T048 全部落地）：

| 模块 | v1（迁移前） | v2（官方标准构件，现行） |
|---|---|---|
| 意图识别 | 手写 JSON 解析（`_parse_llm_json`）+ 条件边 | `with_structured_output`（IntentType 枚举）+ 条件边；关键词兜底保留为降级路径 |
| 多步调度 | planner 一次性计划 + step_executor 游标循环 | supervisor 节点：结构化输出 RoutingDecision + `Command(goto=...)` 动态路由（官方 multi-agent 模式） |
| Worker Agent | AgentDefinition + 手写 ReAct 循环（runner.py） | `create_agent(model, tools, system_prompt, response_format)`（`langchain.agents`，官方现行标准）子图 |
| 单领域路径 | 手写 ReactAgentNode + should_continue | `create_agent` 子图（工具循环内置，无需手写 tools_condition） |
| 工具定义 | 自研 BaseTool（input_schema / output_schema / execute / to_openai_tool） | `@tool` 装饰器（官方主推）/ `langchain_core.tools.BaseTool` 子类（args_schema + `_arun`，有状态工具） |
| 工具执行 | 自研 ToolExecutor（超时/重试/熔断集中层） | 官方 `ToolNode`（`handle_tool_errors`）+ Runnable `.with_retry()` / `.with_fallbacks()` |
| 结构化输出 | 手写 JSON 解析 ×4 处（意图/规划/Worker/合规） | `with_structured_output` / `response_format` |
| 长期记忆 | 自研 Qdrant collection + 注入管线 | LangGraph `Store`（InMemoryStore / AsyncPostgresStore + 内建向量 index） |
| 轨迹 / Token 统计 | 自研 tool_trace 状态字段 + phase_ainvoke 包装 | `BaseCallbackHandler`（标准扩展点）+ 从 messages 中 ToolMessage 派生 |
| HITL / 短期记忆 | `interrupt`/`Command(resume)` + Checkpointer（已标准） | 不变 |

**标准 vs 自研边界**——v2 仅保留的自研项（必要性论证见 ADR-007）：

| 自研项 | 保留理由 |
|---|---|
| 熔断器（连续失败 → 熔断 → 半开探测） | LangGraph/LangChain 无对应物，Phase 3 容错硬需求 |
| 工具结果缓存白名单（T028） | 框架无工具级结果缓存标准 |
| 领域工具、Prompt、各降级规则 | 业务内容而非框架替代（关键词兜底 / 确定性合规判决 / summary 降级等） |

对应删除的自研层：ToolOutput 信封、ToolRegistry、ToolExecutor 集中层、AgentDefinition、
两处手写 ReAct 循环、四处手写 JSON 解析、tool_trace/agent_steps 状态字段、
phase_ainvoke 包装、Qdrant 长期记忆管线。

---

## 3. Agent 角色定义

> **v2 表达**：Orchestrator = 主图上的 **supervisor 节点**（计划 + 动态路由）；
> 每个 Worker = **`create_react_agent` 子图**（system prompt + 工具集 + response_format 结构化输出，
> 见 5.3）。角色职责、工具集、输出 schema 定义不变，仅承载方式从自研定义类换为官方 prebuilt。

### 3.1 Orchestrator Agent（调度代理）

**职责**：
- 理解用户意图，进行任务分类
- 制定多步骤执行计划
- 调度 Worker Agent 并收集结果
- 整合多源信息，生成最终回答
- 判断是否需要人工介入

**核心能力**：
- 意图识别（Intent Classification）
- 任务规划（Task Planning）
- 结果整合（Result Aggregation）
- 异常处理与重试决策

**输入**：用户对话历史 + 当前共享状态
**输出**：下一步行动（调用哪个 Agent / 直接回答 / 转人工）

### 3.2 Claim Agent（理赔核算代理）

**职责**：处理与保单和理赔金额相关的查询

**工具集**：

| 工具名 | 类型 | 功能描述 |
|---|---|---|
| `policy_query` | API 调用 | 根据保单号/身份证查询保单详情（险种、保额、生效日期、免赔额） |
| `claim_calculator` | 计算工具 | 根据诊断、保额、免赔额、赔付比例计算预估赔付金额 |
| `claim_rule_rag` | RAG 检索 | 检索理赔规则知识库（免责条款、赔付比例表、等待期规则等） |
| `claim_status_query` | API 调用 | 查询理赔申请进度 |

**输入**：用户问题 + 保单标识信息 + 医疗审核结果（如有）
**输出**：理赔核算结果（赔付金额、赔付比例、免赔说明）

### 3.3 Medical Agent（医疗审核代理）

**职责**：处理医疗相关的信息核对与审核

**工具集**：

| 工具名 | 类型 | 功能描述 |
|---|---|---|
| `medical_record_query` | API 调用 | 查询用户就诊记录（诊断、处方、检查结果） |
| `diagnosis_matcher` | 规则引擎 | 将诊断描述与 ICD-10 编码匹配，判断是否在保障范围内 |
| `ocr_extract` | OCR 服务 | 提取用户上传的诊断证明、发票等图片中的结构化信息 |
| `medical_kb_rag` | RAG 检索 | 检索医学知识库（疾病定义、治疗方案合理性判断） |

**输入**：用户问题 + 就诊信息 + 上传的图片材料
**输出**：医疗审核结论（是否在保障范围内、材料是否齐全、有无疑点）

### 3.4 Compliance Agent（合规风控代理）

**职责**：对所有输出内容进行合规审查，拦截风险

**工具集**：

| 工具名 | 类型 | 功能描述 |
|---|---|---|
| `compliance_rule_check` | 规则引擎 | 检查回答内容是否包含违规话术（承诺赔付、绝对化用语等） |
| `sensitive_data_filter` | 过滤工具 | 脱敏处理敏感信息（身份证号、银行卡号、病历隐私） |
| `risk_scoring` | 评分模型 | 对对话内容进行风险评分（欺诈风险、投诉风险） |
| `compliance_kb_rag` | RAG 检索 | 检索合规知识库（监管要求、行业规范、公司制度） |

**输入**：待输出的回答内容 + 对话上下文
**输出**：合规审查结果（通过 / 需修改 / 拦截）+ 修改建议

**特殊机制**：Compliance Agent 拥有**一票否决权**——任何输出必须经过合规审查，不通过则不能返回给用户。

---

## 4. 工具设计规范

> **T044 已实施**：工具层全面换用 `langchain_core.tools` 标准构件；v1 的自研 BaseTool /
> ToolOutput 信封已删除，ToolRegistry / ToolExecutor 保留为兼容壳（消费端 T047 迁移完成，
> 待删除）；熔断器与缓存白名单为守卫层最小自研（tools/guards.py）。

### 4.1 工具接口标准（已按 langchain-core 1.6.0 验证）

官方主推 **`@tool` 装饰器**：函数签名类型注解即入参 schema，docstring 即工具描述：

```python
from langchain_core.tools import tool


class PolicyQueryInput(BaseModel):
    """保单查询入参：policy_no 与 id_card 至少提供一个。"""
    policy_no: str | None = None
    id_card: str | None = None


@tool(args_schema=PolicyQueryInput)
async def policy_query(
    policy_no: str | None = None, id_card: str | None = None
) -> dict:
    """根据保单号或身份证号查询保单详情……用户询问'我的保单'、'能赔多少'时使用。"""
    ...  # 业务逻辑：查询保单并返回结果 dict
```

需要 **依赖注入 / 有状态** 的工具（如注入 DB 会话工厂）用 `BaseTool` 子类（1.6.0 验证有效：
`args_schema` 为合法模型字段，`_arun` 返回 `str | dict`，支持 artifact）：

```python
class PolicyQueryTool(BaseTool):
    name: str = "policy_query"
    description: str = "根据保单号或身份证号查询保单详情……"
    args_schema: type[PolicyQueryInput] = PolicyQueryInput

    async def _arun(self, *, policy_no: str | None = None, id_card: str | None = None) -> dict:
        ...
```

- **入参校验**：框架按 schema 自动完成（v1 的手动 `model_validate` 删除）
- **OpenAI 工具 schema**：`bind_tools` 自动生成（v1 的 `to_openai_tool()` 删除；
  `convert_to_openai_tool` 在 langchain-core 1.x 已移除，不再提）
- **失败语义**：业务失败（保单不存在等）作为正常返回内容，由 LLM 向用户解释；
  系统异常抛出，由 `ToolNode(handle_tool_errors=...)` 统一转 ToolMessage 错误回执，不中断图
- **上下文访问**（官方 1.x 能力）：工具签名可加 `runtime: ToolRuntime` 访问图状态 /
  Store / config（模型不可见该参数），替代 v1 经执行器透传上下文的做法
- **装配**：图工厂函数直接构造工具实例列表传给 `create_agent(tools=...)`；
  v1 的 ToolRegistry 删除，测试 mock 经工厂参数注入
- **Adapter 模式保留**：第三方系统（保单/医疗 API、RAG、OCR）仍经 Adapter 封装，挂接在工具实现内部

### 4.2 工具执行与保障

执行：官方 `ToolNode`（`create_react_agent` 内置）。保障机制按官方构件分层承载：

| 机制 | v2 承载（官方/标准） | 对应 v1 |
|---|---|---|
| 入参校验 | 框架按 args_schema 校验 | BaseTool.execute 手动校验 |
| 错误回执 | `ToolNode(handle_tool_errors=...)` | ToolOutput 信封 error_message |
| 重试 | Runnable `.with_retry()`（指数退避） | ToolExecutor 内置重试 |
| 降级 | Runnable `.with_fallbacks()` | ToolExecutor fallback 参数 |
| 超时 | `asyncio.timeout` 包在 `_arun` 内（stdlib，无自研层） | ToolExecutor 超时控制 |
| 熔断 | **自研轻量包装（保留）**——框架无对应物，见 ADR-007 | ToolExecutor 熔断器 |
| 结果缓存 | **自研白名单缓存（保留）**——框架无工具级缓存标准 | T028 工具缓存 |
| 指标 / 追踪 / 调用日志 | `BaseCallbackHandler`（标准扩展点）+ 既有 Prometheus / OTel | ToolExecutor 内埋点 |

---

## 5. 工作流设计（基于 LangGraph）

> **T047 已实施**：5.1–5.4 即现行设计（supervisor 动态路由 + create_agent 子图）；
> 原 v1 对照节（旧主图与 planner/step_executor 设计）已随迁移移除，原貌见 Git 历史。

### 5.1 状态定义

```python
class AgentState(TypedDict, total=False):
    """主图状态（v2 精简：调度改 supervisor 动态路由，轨迹由 messages 派生）。"""

    conversation_id: str
    messages: Annotated[list[AnyMessage], add_messages]  # 对话主通道（checkpoint 持久化）
    intent: str | None                                    # 意图枚举值（IntentType）
    task_plan: list[TaskStep]                             # supervisor 计划（可重规划）
    shared_data: Annotated[dict, merge_shared_data]       # 各 Worker 结论（reducer 合并）
    final_answer: str
    compliance_result: dict | None                        # ComplianceVerdict
    compliance_rounds: int                                # MODIFY 修订闭环轮数（防死循环）
    memory_context: str                                   # Store 检索的跨会话记忆（见 6）
```

相对 v1 删除的字段：
- `current_step`（游标）——supervisor 动态路由取代固定循环
- `tool_trace` / `agent_steps`——从 messages 中 ToolMessage 派生 + callbacks 归集
  （A06 used_tools 与审计口径不变）
- `medical_result` / `claim_result` / `need_human_intervention` 等——v1 已实际由
  shared_data / compliance_result / interrupt 承担，v2 状态收敛到实际使用的键

### 5.2 主图

```
__start__ → intent（意图枚举，条件边分流）
  ├─ complex_consult（complex_consult）→ supervisor
  │     ├─ Command(goto="claim")   → claim 子图   ──→ supervisor（循环）
  │     ├─ Command(goto="medical") → medical 子图 ──→ supervisor（循环）
  │     └─ Command(goto="FINISH")  → synthesize
  ├─ simple_faq → rag → synthesize
  └─ 其他（single_domain / chitchat / other）→ react 子图 → compliance
synthesize → compliance（F10 必经门禁，条件边保证无旁路出口）
compliance ─┬─ PASS → __end__
            ├─ MODIFY（未达轮数上限）→ revise_answer → compliance（复审闭环）
            └─ REJECT → human_review（interrupt 挂起；坐席 Command(resume=结论)
                        恢复 → 合规复审 → __end__）
```

与 v1 的结构差异：complex_consult 路径的 planner + step_executor 游标循环并入 **supervisor 循环**
（Command 动态路由）；Worker 从"节点内函数调用"变为**图上子图节点**；
react 路径的手写循环换 prebuilt 子图。合规门禁与 HITL 结构不变（已是官方标准）。

### 5.3 节点标准机制

#### Intent（意图识别）
官方 Routing 模式：**LLM 结构化输出（枚举）+ 条件边**。

```python
class IntentType(str, Enum):
    simple_faq = "simple_faq"
    single_domain = "single_domain"
    complex_consult = "complex_consult"  # 复杂理赔咨询（v1 名 multi_step，D023 更名）
    chitchat = "chitchat"
    other = "other"


class IntentClassification(BaseModel):
    intent: IntentType
    reason: str


structured = get_chat_model(temperature=0.0).with_structured_output(IntentClassification)
result = await structured.ainvoke([HumanMessage(content=INTENT_CLASSIFICATION_PROMPT.format(...))])
# 节点返回 {"intent": result.intent.value}；条件边按 intent 分发（v1 已是条件边，仅解析层原生化）
```

降级保留：`with_structured_output` 抛错 / 输出非法 → try/except 走关键词规则兜底（规则集与 v1 一致）。

#### Supervisor（调度）
官方 multi-agent supervisor 模式（核心 API 实现，不引 langgraph-supervisor 三方包——
核心 API 模式即官方文档标准，且避免额外依赖）：

```python
class RoutingDecision(BaseModel):
    next: Literal["claim", "medical", "FINISH"]
    plan: list[TaskStep]   # 首轮产出完整计划；后续轮可重规划（replan）
    reason: str


async def supervisor_node(state: AgentState) -> Command:
    decision = await structured_model.ainvoke(...)  # 输入：用户诉求 + 计划进度 + shared_data
    return Command(goto=decision.next, update={"task_plan": decision.plan, ...})
```

- 首轮产出计划；每轮依据计划进度与 shared_data 决定下一步；步骤失败可重规划
  （v1 5.6 承诺的"动态调整"未实现，v2 由 supervisor 落实）
- 防失控：`recursion_limit`（替代 v1 的 current_step 游标与 MAX_TOOL_ROUNDS）
- Worker 结论经子图返回写入 shared_data（reducer 合并），供后续步骤与整合节点读取

#### Worker 子图（claim / medical）
官方现行标准 **`langchain.agents.create_agent`**（LangChain 1.x 起为 agent 构造标准；
`langgraph.prebuilt.create_react_agent` 自 LangGraph 1.0 起标记 deprecated，虽然已装可用但不采用。
`create_agent` 需新增 `langchain>=1.0` 依赖，列入 T044）：

```python
from langchain.agents import create_agent

claim_agent = create_agent(
    model=get_chat_model(),
    tools=[policy_query, claim_calculator, claim_rule_rag, claim_status_query],
    system_prompt=CLAIM_AGENT_PROMPT,          # 静态系统提示
    response_format=ClaimAgentOutput,          # 结构化终局输出 → state["structured_response"]
    checkpointer=...,                          # 子图按需挂 checkpoint（主图已挂则不必）
)
# 调用侧（step 包装节点）以输入 messages 注入动态任务指令与 shared_data：
await claim_agent.ainvoke({
    "messages": [HumanMessage(content=_task_instruction(instruction, shared_data))]
})
```

- `AgentDefinition` 三要素映射：system_prompt / tools / response_format（输出 schema）
- 动态任务指令 + shared_data 经**输入 messages** 注入（调用侧构造），无需动态 prompt 钩子；
  如需模型前/后处理钩子，官方机制是 `middleware`（替代旧 pre_model_hook / post_model_hook）
- 工具循环（LLM ↔ ToolNode + tools_condition）由 prebuilt 内置，无需手写
- `response_format` 替代 v1 手写 `_parse_agent_json` + schema 校验
- 降级保留：解析失败 → 包装节点 catch → `{"summary": 原文前 500 字}`（v1 语义）

#### React 子图（单领域 / 闲聊 / 其他）
`create_agent` 通用助手（全量工具），工具循环内置；
v1 手写 ReactAgentNode 与 should_continue 删除。
LLM 故障降级话术（T022 语义）经 `.with_fallbacks()` 或外层包装节点保留。

#### Compliance（合规门禁，ADR-002 不变）
结构化判决 + 三态条件边：

```python
class ComplianceVerdict(BaseModel):
    verdict: Literal["PASS", "MODIFY", "REJECT"]
    violations: list[str]
    suggestion: str
    risk_score: float
```

`with_structured_output(ComplianceVerdict)` + 条件边三态流转（PASS → END；
MODIFY → revise_answer → 复审闭环；REJECT → human_review）。
所有输出路径必经 compliance 的条件边汇聚结构不变；LLM 失败的确定性兜底判决规则原样保留。

#### Human Review（HITL，不变）
`interrupt()` 挂起 + 坐席 `Command(resume=...)` 恢复——v1 已是官方标准用法。

#### Synthesize（整合，不变）
普通节点：汇总 shared_data 生成最终回答；LLM 失败降级为各数据源 summary 拼接（v1 语义保留）。

### 5.4 降级语义对照（v1 → v2）

每一条 v1 手写兜底在 v2 都有等价承载，一条不丢：

| 决策点 | v1 手写兜底 | v2 承载方式 |
|---|---|---|
| 意图识别 | `_parse_llm_json` + 关键词规则 | `with_structured_output` try/except + 同一套关键词规则 |
| 任务规划 | `_parse_llm_json` + 关键词计划 | supervisor 解析失败 → 关键词计划（同规则） |
| Worker 输出 | `_parse_agent_json` → summary 降级 | `response_format` 失败 catch → summary 降级 |
| 合规判决 | `_parse_llm_json` + 确定性判决 | `with_structured_output` 失败 → 确定性判决（同规则） |
| 单 Agent LLM 故障 | 节点 try/except 降级话术 | `.with_fallbacks()` / 包装节点（同话术） |
| 整合节点 LLM 故障 | summary 确定性拼接 | 不变（普通节点 try/except） |

## 6. 记忆机制

### 6.1 三层记忆架构

```
┌─────────────────────────────────────────────────┐
│  短期记忆（Short-term Memory）                   │
│  └─ 当前对话窗口内的消息历史                      │
│  └─ 滑动窗口策略（最近 20 条 / 4K tokens）       │
│  └─ 存储在 State 中，随会话结束而清除             │
├─────────────────────────────────────────────────┤
│  工作记忆（Working Memory）                      │
│  └─ 当前任务的结构化信息提取                     │
│  └─ 保单号、诊断信息、赔付金额等关键实体         │
│  └─ 从对话中抽取，存储在 user_profile 中         │
├─────────────────────────────────────────────────┤
│  长期记忆（Long-term Memory）                    │
│  └─ 用户历史对话摘要（向量存储）                 │
│  └─ 关键实体（保单号/诊断/金额）随摘要入库       │
│  └─ 存储在 Qdrant，按 user_id 检索               │
└─────────────────────────────────────────────────┘
```

> **落地（T034/T035）**：独立 collection `long_term_memory`；每 N 轮（默认 3）或转人工终态
> 由 LLM 生成摘要 + 实体（失败降级正则提取），point id = uuid5(conversation_id) 幂等覆盖；
> 新会话首轮按 user_id filter 检索 top-2（min_score 0.4 噪声过滤）注入 system prompt
> （Token 预算 1200 字符），实测「我上次问的那张保单」跨会话正确引用历史保单号与金额。

### 6.2 记忆管理策略

| 记忆类型 | 存储介质（实际落地） | 生命周期 | 检索方式 |
|---|---|---|---|
| 短期记忆 | LangGraph State + Checkpoint（dev=InMemorySaver / prod=PostgreSQLSaver） | 会话内多轮 | 直接读取 |
| 工作记忆 | State 的 shared_data（Agent 结论）+ 审计表 messages（tool_trace/agent_steps） | 单轮任务 / 会话级审计 | 按会话 ID 查询 |
| 长期记忆 | Qdrant 独立 collection `long_term_memory`（摘要 + 实体）；**v2 目标：LangGraph Store**（见下方注记） | 跨会话持久 | user_id filter + 向量相似度检索（T034/T035） |

> 落地说明：设计期的"工作记忆 MySQL"未单独建表——其职责由 shared_data（Agent 间传递）
> 与 messages 审计字段（tool_trace/agent_steps）分担；业务库实际为 SQLite（dev）/ PostgreSQL（prod）。

> **T048 已实施**：长期记忆收敛到官方 **LangGraph Store** 体系——dev=`InMemoryStore`
> （`langgraph.store.memory`）、prod=`AsyncPostgresStore`（`langgraph.store.postgres`，
> langgraph 主包提供，复用 psycopg 驱动），namespace 按 `(user_id,)` 隔离，
> 向量检索用 Store 内建 index 配置（`IndexConfig`：`dims` / `embed` / `fields`，
> embed 仍用 BGE-M3——以上均按 langgraph 1.2.11 实装源码验证）。
> T034/T035 的摘要 + 实体写入逻辑保留，
> 落点改为 `store.put`；检索注入改为节点内 `store.get/search`（官方 cross-thread memory 模式）。
> 自研 Qdrant `long_term_memory` collection 与注入管线删除；**Qdrant 仅保留 RAG 知识库用途**。
> 短期记忆（Checkpointer）不变；工作记忆的审计字段随 5.1 状态精简改由 messages 派生。

### 6.3 Token 预算控制

长对话场景下 Token 消耗是重要成本项，采用以下策略：

1. **滑动窗口**：只保留最近 N 条消息，超出部分截断
2. **摘要压缩**：对话超过阈值时，对历史消息生成摘要，用摘要替代原始对话
3. **实体提取**：从历史对话中提取结构化实体（保单号、诊断等），后续引用实体而非原文
4. **分级模型**：主链路 deepseek-v4-flash，OCR 专职 vision-exp（失败降级 Mock），
   glm-5.3-flash 为跨供应商容灾变体（ADR-006）

---

## 7. 容错与可靠性

### 7.1 三级容错机制

```
一级：工具调用重试（瞬时故障）
    │  └─ 网络超时、API 限流、偶发 5xx
    │  └─ 策略：指数退避重试，最多 2 次
    ▼
二级：Fallback 降级（工具不可用）
    │  └─ 第三方系统挂了、工具熔断了
    │  └─ 策略：提供降级方案（如 RAG 不可用 → 返回兜底回答模板）
    ▼
三级：人工介入（系统无法处理）
       └─ 多轮重试后仍失败、合规拦截、高风险场景
       └─ 策略：收集好所有信息，转人工客服，并附带系统整理的上下文
```

> **v2 映射**：一级（工具级）→ Runnable `.with_retry()` + 熔断器（自研保留项，4.2）；
> 二级（降级）→ `.with_fallbacks()` + 各节点降级路径（5.4 对照表）；
> 三级（人工介入）→ `interrupt` 转人工（已是官方标准，不变）。

### 7.2 熔断设计（Circuit Breaker）

对每个外部工具独立维护熔断器状态：

- **Closed（关闭）**：正常调用，统计失败率
- **Open（打开）**：失败率超过阈值（如 50%），直接拒绝调用，返回 Fallback
- **Half-Open（半开）**：熔断一段时间（如 30s）后，放行少量请求探测
  - 成功 → 关闭熔断器
  - 失败 → 继续熔断

### 7.3 异常分类与处理

| 异常类型 | 处理方式 | 用户感知 |
|---|---|---|
| LLM 调用超时 | 重试 1 次，仍失败则转人工 | "系统繁忙，正在为您转接人工客服" |
| 工具 API 报错 | 重试 + Fallback | 不感知或提示"部分信息暂不可用" |
| 工具结果异常 | 结果校验 + 二次调用确认 | 不感知 |
| 合规审查不通过 | 修改后重审，仍不通过则转人工 | 不感知或由人工回复 |
| 用户意图不明 | 主动追问澄清 | 反问用户需求 |

---

## 8. 可观测性

### 8.1 监控指标体系

| 类别 | 指标 | 说明 |
|---|---|---|
| 业务指标 | 任务完成率 | 成功处理的复杂任务占比 |
| | 人工转接率 | 需要转人工的对话占比 |
| | 平均处理时长 | 从用户提问到最终回答的耗时 |
| 工具指标 | 工具调用成功率 | 成功调用次数 / 总调用次数 |
| | 工具平均耗时 | 每个工具的 P50 / P95 / P99 延迟 |
| | 熔断次数 | 各工具触发熔断的次数 |
| Agent 指标 | 工具调用准确率 | LLM 选择正确工具的比例 |
| | 计划执行成功率 | 按计划完成所有步骤的比例 |
| | 重试率 | 需要重试的步骤占比 |
| 成本指标 | Token 消耗 | 每轮对话 / 每日总 Token 数 |
| | 平均对话成本 | 每轮对话的预估费用 |
| 合规指标 | 合规拦截率 | 被合规节点拦截的回答占比 |
| | 违规漏检率 | 人工抽检发现的漏检比例 |

### 8.2 实现方式

- **指标采集**：Prometheus client 埋点，每个关键节点记录耗时和结果
- **可视化**：Grafana 仪表盘，实时监控核心指标
- **告警**：Prometheus Alertmanager，关键指标异常触发告警（如人工转接率突增、工具熔断）
- **Trace**：OpenTelemetry 全链路追踪，可追溯单轮对话的完整执行路径

### 8.3 日志规范

每轮对话生成一条结构化日志，包含：
- 对话 ID、用户 ID、时间戳
- 意图分类结果
- 执行计划（如有）
- 每一步的 Agent 调用记录（入参、出参、耗时、是否成功）
- 工具调用明细
- 最终回答
- Token 消耗统计

---

## 9. 评测体系

> Phase 7（D033，2026-09-03，依据 `docs/eval-audit-for-ai.md` 审计）强化后的口径。

### 9.1 评测维度

| 维度 | 指标 | 评测方式 |
|---|---|---|
| 任务完成率 | 复杂任务端到端成功率 + **Wilson 95% CI**（n=200 时半宽约 ±4.5pp，区分信号与噪声） | 人工标注测试集 + 自动判分 |
| 转人工质量 | **human_recall**（期望转人工中被转占比，北极星"62%→37%"主口径）/ **human_precision**（实际转中确实该转占比） | human_handoff 类目 18 条（骗保/材料缺失/情绪激动/法律纠纷/主动要求五类，T065） |
| 意图准确率 | 意图分类正确占比（F03 ≥90% 验收） | expected_intent 标注 80 条（T066 激活） |
| 工具调用准确率 | LLM 选择正确工具的比例（scored=0 显 N/A） | 构造测试用例，自动评估 |
| 数值正确性 | **expected_numbers 精确断言**（数字归一化 + 边界匹配：640 不混过 4640，金额算错必挂） | 金额/天数/比例类用例（T067） |
| 回答质量（二层） | **LLM-as-Judge** rubric 三维（事实一致/完整/合规）0-2 分、≥4 判过——独立口径不并入 passed | judge 判 must_include 为空用例；人工抽检 50 条对齐率 ≥85% 方可采信（T068） |
| 轨迹质量 | 五维独立报告（顺序 LCS/禁调/路由/入参子集/次数上限+冗余计数） | order 64 + route 34 条标注（T069，真实轨迹参考+规则派生） |
| 会话记忆 | 多轮指代消解/追问/改口重算/冲突纠正 | multiturn 数据集 30 条，逐轮同 thread 末轮判分（T070） |
| 安全性 | 注入/越权/PII 诱导/违规承诺红线 + 错别字方言鲁棒性 | adversarial 数据集 20 条，红线断言具体违规输出物（T071）；合规通过率 |
| 效率 | 平均响应时间 + **p95 耗时** + **token/用例**（Prometheus 差分） | 性能压测 + 线上统计 |
| 回归防线 | **CI 门禁**：PR smoke 20 条，完成率降幅 >5pp 拦截（无 LLM_API_KEY 时 skip） | check_eval_gate.py + eval-gate job（T072） |

### 9.2 测试集构建

| 数据集 | 规模 | 内容 |
|---|---|---|
| `eval_dataset.json`（主，v1.1.0） | **218 条** | FAQ 30 / 单领域 60 / 多步 80 / 边界 30 / **转人工 18**（北极星分母） |
| `eval_graph_assoc.json`（v1.1.0） | 24 条 | 复杂关联（类目已修正为 graph_assoc，T069） |
| `eval_multiturn.json` | 30 条 | 多轮四场景：指代/追问/改口/冲突纠正（turns 字段，T070） |
| `eval_adversarial.json` | 20 条 | 安全对抗五类各 4 条（独立数据集不污染主基线，T071） |

- 标注标准：每条用例包含「用户输入 + 期望工具调用序列 + 期望回答要点」；
  数值类必标 `expected_numbers`（精确断言）；红线类必标 `must_not_include`（具体违规输出物，否定语境不误杀）

### 9.3 评测流程

1. **自动化评测**：每次代码变更后自动跑测试集，输出核心指标（`--judge` 开启二层判分）
2. **CI 回归门禁**：PR 触发 smoke 20 条，完成率降幅 >5pp 阻断合并（T072）
3. **回归对比**：与基线报告（含 Wilson CI）对比，区分真实回退与噪声
4. **人工抽检**：judge 校准——抽检 50 条与 LLM 判分对齐率 ≥85% 方可采信（T068）
5. **线上 A/B**：重大变更先灰度 10% 流量，对比线上指标

---

## 10. 技术选型

| 类别 | 选型 | 理由 |
|---|---|---|
| Agent 框架 | LangGraph | 生产级状态机、支持 Checkpoint、社区活跃 |
| Web 框架 | FastAPI | 异步支持好、类型安全、生态成熟 |
| 向量数据库 | Qdrant | 单容器轻量部署、local mode 免容器开发、规模匹配项目需求（详见 ADR-004） |
| 关系数据库 | PostgreSQL | 可靠、支持 JSON、LangGraph Checkpoint 原生支持 |
| 缓存 | Redis | 会话缓存、工具结果缓存、限流 |
| LLM | 主链路：deepseek-v4-flash；图片 OCR 专职：deepseek-v4-flash-vision-exp（失败降级 Mock，详见 ADR-005） | 两者价格相同、均支持 function calling；主链路用正式版稳定，vision-exp 提供真实多模态 OCR |
| Embedding | BGE-M3 | 中文效果好、多语言支持 |
| 重排序 | BGE-Reranker-v2-m3（T043 落地，`RERANK_ENABLED` 默认关） | 与 BGE-M3 同生态；实测 top1 0/5 变化、次序去重改善、完成率 +3.3pp 不显著——小语料下默认关，语料扩大后开启（D020）；Qwen3-Reranker 系列经决策树对照排除（4B 纯 CPU 不可行、0.6B 生态/延迟劣于 bge） |
| 部署 | Docker + Docker Compose | 本地开发和生产部署一致 |
| 监控 | Prometheus + Grafana | 标准选型、生态成熟 |
| 包管理 | uv | 速度快、锁文件可靠 |

---

## 11. 开发路线图

### Phase 1：MVP（核心流程跑通）— ✅ 已交付（2026-08-25，T001-T014）
- [x] 项目脚手架（FastAPI + LangGraph + Docker Compose + CI）
- [x] 4 Agent 定义 + Prompt 体系；9 工具（保单/计算器/RAG/就诊/ICD-10/OCR/规则/评分/脱敏）
- [x] 单 Agent ReAct 核心流程 + 完整主图（intent 分流 → 多步/RAG/ReAct → 合规门禁）
- [x] Gradio 演示界面 + A02-A07 API

### Phase 2：多智能体协作 — ✅ 已交付（2026-08-25，T015-T022）
- [x] Orchestrator-Worker 协作（规划 → 步骤执行循环 → 整合）
- [x] 合规三态流转（PASS/MODIFY 修订闭环/REJECT 拦截）+ 端到端场景测试

### Phase 3：工程化与优化 — ✅ 已交付（2026-08-25，T023-T030）
- [x] 容错（超时/重试/熔断/全链路降级）+ Prometheus/Grafana 监控
- [x] 评测体系（200 条溯源测试集 + 运行器，基线 89.5%）+ 工具缓存 + Token 预算

### Phase 4：深度与亮点 — ✅ 已交付（2026-08-26/27，T031-T041）
- [x] GraphRAG（T031-T033：知识图谱 106 实体/116 关系 + 混合召回 + 24 条对比评测）
- [x] 长期记忆（T034/T035：摘要+实体向量化入库，user_id 隔离，首轮注入）
- [x] HITL 人工介入（T036-T038：工单后端 + LangGraph interrupt 恢复 + Next.js 坐席工作台）
- [x] OTel 全链路追踪（T039：tracing profile，单轮 25 span 调用树）
- [x] A/B 实验框架与实战（T040/T041：变体注册表 + z 检验，glm-5.3-flash 跨供应商对比，结论见 ADR-006）
- （合规风控模型优化按 D017 决策不纳入：规则引擎红线违规 0/200 已达标）

### Phase 5：LangGraph 标准构件对齐重构 — ✅ 已交付（2026-09-01/02，T044-T048，D021/ADR-007）

- [x] T044 工具层标准化：9 个工具迁移官方工具定义（`ClaimflowTool` 继承 `langchain_core.tools.BaseTool`，
      args_schema + _arun 返回 dict），重试换官方 `.with_retry()`、超时 `asyncio.timeout`；
      守卫下沉工具层（`tools/guards.py` GuardedTool：熔断 + 缓存白名单 + 超时，agent 循环内外统一生效）；
      `tools/factory.py` 工厂装配替代全局注册；AGENTS.md 6.1 同步修订
- [x] T045 决策点结构化输出原生化：意图（IntentType StrEnum）/ 合规判决（ComplianceVerdict Literal）
      改 `with_structured_output`，手写 `_parse_llm_json` ×2 删除，关键词 / 确定性兜底保留；
      意图 multi_step → complex_consult 更名（D023，全链含历史值映射）
- [x] T046 Worker 子图化：AgentDefinition → `langchain.agents.create_agent` 子图
      （system_prompt + 输入 messages 注入 shared_data + response_format → structured_response），
      手写 ReAct 循环删除；tool_trace 改 messages 派生
- [x] T047 supervisor 动态路由化：planner + step_executor 删除，supervisor 节点
      （RoutingDecision + `Command(goto)` + 计划对账守卫，支持执行中重规划）；
      react 路径换 `create_agent` 子图；State 精简（used_tools/agent_steps 改 messages/task_plan 派生）
- [x] T048 长期记忆 Store 化 + 全量回归：迁官方 Store（dev=InMemoryStore / prod=AsyncPostgresStore +
      内建向量 index），删 Qdrant 记忆 collection；200 条评测基线回归（报告见
      evals/reports/t048_phase5_regression.json）；文档回填"已实施"、README 架构图更新

> 依赖前置随 T044 完成：langchain 1.3.18 新增（create_agent 官方标准）+ langchain-core 1.6.1。

---

## 附录：关键技术决策记录

### ADR-001：选择 LangGraph 而非 CrewAI / AutoGen
- **背景**：多 Agent 框架选型
- **选项**：LangGraph / CrewAI / AutoGen
- **决策**：LangGraph
- **理由**：
  1. LangGraph 是状态机模型，可控性强，适合生产环境
  2. 原生支持 Checkpoint，可持久化对话状态，支持中断恢复
  3. 与 LangChain 生态深度集成，工具、内存、Chain 复用方便
  4. CrewAI 偏角色扮演，对流程控制力弱；AutoGen 偏研究，生产化程度低

### ADR-002：合规审查作为独立 Agent 而非工具
- **背景**：合规审查放在哪里
- **选项**：作为工具 vs 作为独立 Agent
- **决策**：独立 Agent
- **理由**：
  1. 合规需要独立判断视角，不能被业务 Agent 的目标带偏
  2. 独立 Agent 有自己的系统提示词和工具，职责更清晰
  3. 一票否决权需要架构层面保障，作为工具可能被绕过

### ADR-003：Milvus 而非 Qdrant（已被 ADR-004 取代）
- **背景**：向量数据库选型
- **选项**：Milvus / Qdrant
- **决策**：Milvus（初版决策，2026-08-24 复审后推翻，见 ADR-004）
- **理由**：
  1. Milvus 分布式架构更成熟，支持大规模（亿级向量）部署
  2. 国内社区活跃，中文资料多
  3. 后续如果做业务升级，Milvus 的扩展性更好
  4. （如果项目规模小或运维资源有限，Qdrant 也完全够用，可灵活调整）

### ADR-004：改用 Qdrant 取代 Milvus
- **背景**：复审向量数据库选型。项目为 PoC 级求职作品集，RAG 知识库仅 10-20 篇文档（千级以下向量）；开发者本地 Docker 环境因 WSL2/HCS 异常不可用，容器化验证依赖 GitHub Actions CI
- **选项**：维持 Milvus / 切换 Qdrant
- **决策**：Qdrant
- **理由**：
  1. 部署成本：Milvus standalone 需 etcd + MinIO + Milvus 三个容器（建议内存 4GB+）；Qdrant 单容器即可（几百 MB 内存），CI 起服务更快更稳
  2. 开发体验：Qdrant 支持 local mode（本地文件模式，零容器零服务），完美适配本地 Docker 不可用的现实；开发与生产共用同一套客户端代码
  3. 规模匹配：本项目向量规模远低于两者上限，选轻量方案更务实；"按规模选型"本身是工程加分项
  4. Milvus 的分布式优势在本项目无落地场景，为其付出的运维成本不产生收益

### ADR-005：LLM 混合模型策略（flash 主链路 + vision-exp 专职 OCR）
- **背景**：2026-08-21 DeepSeek 开放实验性多模态模型 `deepseek-v4-flash-vision-exp`（文本能力与 flash 正式版持平、价格相同、均支持 function calling）。是否将其作为唯一主模型？
- **选项**：全用 vision-exp / 混合策略 / 维持纯 Mock OCR
- **决策**：混合策略——主链路（意图识别/任务规划/工具调用/回答生成）用正式版 `deepseek-v4-flash`；图片 OCR 专职用 `deepseek-v4-flash-vision-exp`，识别失败自动降级预置 Mock 数据
- **理由**：
  1. vision-exp 为实验版本，官方不建议直接用于生产；主链路占 95% 以上调用量，用正式版保证演示稳定性
  2. OCR 仅在图片材料场景调用，vision-exp 失败有 Mock 兜底，单点风险隔离
  3. 与 6.3 节"分级模型"成本策略自洽：高频文本任务与低频视觉任务各用所长
  4. 真实多模态 OCR 相比纯 Mock 是演示亮点，成本几乎不变（单张图片最高折算 384 token）

### ADR-006：跨供应商 LLM 选型结论（T041 实战实验，决策记录 D019）
- **背景**：200 条全量 A/B（evals/reports/t041_glm_20260827_082238）对比 deepseek-v4-flash 与
  glm-5.3-flash（智谱），量化任务完成率/工具准确率/耗时/token 四维
- **决策**：主链路维持 deepseek-v4-flash；glm-5.3-flash 注册为容灾备选变体
- **理由**：质量维度统计等价（完成率 90.5% vs 89.5%、工具准确率 96.3% vs 95.8%，z 检验均不显著），
  切换无质量收益；glm 当前 Key 档位限流明显（串行评测持续 429），token +15%；
  供应商可迁移性经 OpenAI 兼容接口三行配置实证，作为 DeepSeek 故障时的降级路径保留

### ADR-007：架构 v2——全面对齐 LangGraph/LangChain 标准构件（决策记录 D021）
- **背景**：评审指出 v1 的 Agent/工具层偏离官方模式（自研 BaseTool/ToolExecutor/ToolRegistry、
  AgentDefinition + 手写 ReAct 循环、四个 LLM 决策点手写 JSON 解析）；图编排层本身已是官方标准。
  用户确立重构原则：**所有部分尽量按 LangGraph/LangChain 已定义的方法、类、架构实施，非必要不增加自定义内容**
- **选项**：
  A 温和对齐（保留 plan-execute 调度，仅换工具基类与 prebuilt Worker）；
  B 全面原生化（supervisor 动态路由 + create_react_agent 子图 + ToolNode + with_structured_output + 官方 Store）；
  C 维持现状 + 补决策记录说明偏离理由
- **决策**：B（supervisor 调度由用户拍板），文档先行、代码零改动，迁移任务 T044-T048 待确认后执行
- **理由**：
  1. 官方构件享有版本演进与社区验证红利，删除约 500 行自维护框架胶水（信封/注册中心/集中执行器/手写循环/手写解析）
  2. supervisor 是官方 multi-agent 文档标准范式，且落实 v1 承诺未实现的"执行中重规划"
  3. 手写 JSON 解析 ×4 处收敛为 `with_structured_output` / `response_format`，异常路径更短
  4. 长期记忆收敛官方 Store 接口，dev/prod 同构（InMemoryStore / AsyncPostgresStore），删自研 Qdrant 记忆管线
- **仅保留自研（必要性论证）**：
  1. 熔断器——LangGraph/LangChain 无对应物，Phase 3 容错硬需求
  2. 工具结果缓存白名单——框架无工具级结果缓存标准（T028 特性）
  3. 领域工具 / Prompt / 各降级规则——业务内容而非框架替代（关键词兜底、确定性合规判决、summary 降级）
- **影响**：AGENTS.md 6.1/6.2 同步修订（T044 前置）；pyproject langgraph 下限收紧；
  Qdrant 仅保留 RAG 用途；tool_trace/agent_steps 改由 messages 派生 + callbacks 归集；
  200 条评测基线回归（完成率相对 89.5% 回退 ≤1pp 为验收线）

> **验证补记（2026-09-01，D022）**：v2 全部 API 写法已对照官方文档与实锁版本逐一核实
> （langgraph 1.2.11 / langgraph-prebuilt 1.1.0 / langchain-core 1.6.0 / langchain-openai 1.6.0）。
> 验证通过：`Command(goto/update/resume)`、`interrupt`、`ToolNode(handle_tool_errors)`、
> `tools_condition`、`with_structured_output`、`.with_retry` / `.with_fallbacks`、`bind_tools`、
> `BaseTool.args_schema` + `_arun`、`InMemorySaver` / `AsyncPostgresSaver`、
> `Store.index`（IndexConfig：dims/embed/fields）、`response_format → structured_response` 键。
> 三处修正：① `convert_to_openai_tool` 已从 langchain-core 1.x 移除（只保留 bind_tools 提法）；
> ② `AsyncPostgresStore` 实际路径 `langgraph.store.postgres`（langgraph 主包，非 checkpoint-postgres）；
> ③ **`create_react_agent` 自 LangGraph 1.0 起 deprecated**——v2 Worker/单领域子图改用官方现行标准
> `langchain.agents.create_agent`（新增 langchain≥1.0 依赖，动态指令经输入 messages 注入、
> 钩子经 middleware），`@tool` 装饰器为工具定义主推方式。
