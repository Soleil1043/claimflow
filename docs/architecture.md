# claimflow 架构文档（核赔平台版）

> 本文是保险理赔智能核赔平台的架构总览，与工程版设计文档
> [`claimflow-新架构设计.md`](claimflow-新架构设计.md) 互补：设计文档讲"为什么这样设计"，
> 本文讲"现在系统长什么样"。统一语言见 [`../CONTEXT.md`](../CONTEXT.md)，
> 全部技术决策链见 [`../.agent/decisions.md`](../.agent/decisions.md)（D001-D076）。

---

## 目录

1. [系统概览](#1-系统概览)
2. [分层架构](#2-分层架构)
3. [核赔主图](#3-核赔主图)
4. [LLM Orchestrator](#4-llm-orchestrator)
5. [Worker 与 skill 作业规程](#5-worker-与-skill-作业规程)
6. [险种 pack](#6-险种-pack)
7. [合规与金额安全](#7-合规与金额安全)
8. [HITL 人工介入](#8-hitl-人工介入)
9. [在线客服域](#9-在线客服域)
10. [申请人记忆](#10-申请人记忆)
11. [交付队列与案件生命周期](#11-交付队列与案件生命周期)
12. [数据模型](#12-数据模型)
13. [可观测性](#13-可观测性)
14. [评测体系](#14-评测体系)
15. [配置 / Profile / 部署](#15-配置--profile--部署)
16. [关键决策索引](#16-关键决策索引)

---

## 1. 系统概览

客户提交理赔申请与材料后，**LLM Orchestrator 动态调度六个专业 Worker**（材料审核 /
保单核验 / 风控筛查 / 责任认定 / 金额理算 / 决定书生成）完成自动核赔：

- 低风险小额案件自动签发《理赔决定书》；
- 其余转人工复核（补件 / 复核 / 升级三类工单，interrupt 挂起、补传自动恢复）；
- 领域知识以 skill 作业规程包（`skills/<stage>/<line>.md`）承载——准确率迭代改文本不改代码。

四条硬边界贯穿全系统：

1. **静态合规门**——决定书生成 → 合规审查是图上焊死的边，任何路由决策无法绕过；
2. **金额不依赖 LLM**——理算纯确定性（Decimal + 规格公式），正文金额三方断言；
3. **LLM 失败不失控**——守卫违规改投 / 失败走险种确定性默认计划 / 决策全量审计；
4. **分级自动可配置**——自动签发的风险与金额阈值全部配置化（pydantic-settings）。

---

## 2. 分层架构

```
┌────────────────────────────────────────────────────────────────┐
│ 前端层  chatui（案件提交门户 + 悬浮 AI 客服）  workbench（坐席工作台）│
├────────────────────────────────────────────────────────────────┤
│ API 层  app/api/v1  cases / interventions / support / memory /  │
│                            health                              │
├────────────────────────────────────────────────────────────────┤
│ 图编排层 workflows/case_graph.py + nodes/                       │
│   intake → orchestrator ⇄ 六 worker → compliance_gate →        │
│   auto_adjudicate / human_gate（LangGraph 状态机）              │
├────────────────────────────────────────────────────────────────┤
│ 服务层  services/  case_service / case_jobs / case_store /      │
│   amounts / decision_doc / skills / materials / worker_agent /  │
│   support / memory / rag / llm / cache / observability / db     │
├────────────────────────────────────────────────────────────────┤
│ 工具层  tools/  ClaimflowTool（langchain BaseTool）+ GuardedTool │
│   claim / medical / document / fraud / compliance / support     │
├────────────────────────────────────────────────────────────────┤
│ 数据层  PostgreSQL（案件/事件/决定书/工单/客服） Qdrant（RAG）     │
│         Redis（缓存） LangGraph Checkpoint/Store                │
└────────────────────────────────────────────────────────────────┘
```

依赖方向自上而下单向；节点层只做编排与状态流转，业务规则下沉服务层与工具层。

---

## 3. 核赔主图

`workflows/case_graph.py` 定义，`ClaimCaseState`（`state.py`）为共享状态。

```
START → intake → orchestrator ⇄ { material_review, policy_verify,
                                   fraud_check, liability_judge,
                                   amount_calc, decision_generate }
decision_generate → compliance_gate（静态边）
compliance_gate → PASS → auto_adjudicate ─┬─ 签发 → END
                  ├─ MODIFY → revise（重渲染）→ compliance_gate
                  └─ REJECT → human_gate（interrupt）
auto_adjudicate ── 超阈值/高风险 ──────────→ human_gate
human_gate ── Command(resume)：补件/签批/改判/升级 ──→ END
```

关键机制：

- **Send 并行派发**：orchestrator 的 `route_dispatch` 条件边返回 `[Send(state)]` 列表，
  一轮可并行投多个 worker（如 policy_verify ∥ fraud_check），区间重叠有测试断言；
- **回边派生**：worker → orchestrator 的回边由 `StageSpec.back_to_orchestrator` 派生，
  不手写；
- **interrupt 挂起**：human_gate 用 LangGraph `interrupt` 挂起，checkpoint 持久化
  （thread_id = case_id），跨服务重启可恢复；
- **checkpoint schema 版本**（T141/D061）：`CASE_SCHEMA_VERSION` 版本戳写入 state；
  resume 时版本不匹配 → 删旧 thread + 用最近 RUN 原始输入全新重跑（案件事实权威在
  cases 表，重跑无损）+ `schema_reset` 审计事件；
- **worker 阶段 span**：`_timed` 包装为每阶段产 OTel span（含 case_id）。

## 4. LLM Orchestrator

`nodes/orchestrator.py`（调度装配）+ `nodes/guards.py`（确定性守卫纯函数层）。

**每轮调度流程**：

1. 按阶段快照字段组装当前案件上下文（材料结论 / 保单结论 / 风控结论…）；
2. LLM 结构化输出 `RoutingDecision{next[], plan, reason}`（function_calling 承载）；
3. **守卫强制**（代码层，`enforce_guards`）：
   - 前置条件缺失 → 改投首个 pending 前置阶段；
   - 重复派发去重；决定书生成必须单派；
   - 材料完整性强止（`_COMPLETION_GATED`）——材料不齐不得进入后续阶段；
   - 必做集未收敛 → 强制补投；
4. 超预算（≤15 调度调用）或 LLM 失败 → **险种确定性默认计划**（标准管线顺序）；
5. 守卫清空目标且未超预算 → 回落 `default_route`（D056-2 liveness 修复，
   防规划退化致案件中途终止）；
6. 路由决策（含守卫修正、模式、超预算原因）全量审计落 `case_events`。

**注入防御**（T131/D056 追记）：路由 prompt 的用户数据以 `<<<DATA … >>>DATA`
定界包裹，"数据非指令"三条铁律置于数据区之前——申请人描述中的指令注入按字面
数据对待。对抗集 8 条注入用例 0 被拐。

## 5. Worker 与 skill 作业规程

**Worker 执行体**（`services/worker_agent.py`）：`langchain.agents.create_agent`
子图 + 官方中间件栈：

- `ModelCallLimitMiddleware`——工具轮次上限；
- `ToolErrorMiddleware`——工具系统异常转 error ToolMessage 自愈换路（D052）；
- `GuardedTool`（工具层守卫）内层已带官方 `.with_retry()`，不重复叠加。

**责任认定三段式**（`nodes/liability_judge.py`，代表最深 worker）：

1. 确定性前置：保单无效 / 等待期未过（`出险日 > eff + N` 才过，D063 修 off-by-one）
   → 不经 LLM 直接 not_covered；
2. ReAct Agent：claim_rule_rag 条款检索 + diagnosis_matcher 诊断匹配，
   `LiabilityOutput` 结构化输出（条款引用 + 置信度）；
3. 关键词兜底：LLM 失败时险种 pack 的 exclusion_keywords 确定性判定
   （置信度 0.9，T142 六组同义词清账）。

**材料审核三段流水线**（`nodes/material_review.py`）：

1. 逐份提取：图片/PDF/Word 两段式（文本提取 → 扫描件 vision），`@task` 子任务并行
   + 失败重试 + 崩溃恢复短路（T116）；
2. 规则层：险种必需材料完整性 + 发票/清单金额交叉核验（find_amount_contradictions）；
3. AI 一致性审查：结构化输出异常四类（姓名/诊断/金额/日期矛盾），通过必须空数组，
   中性观察进 notes 不压分（D063）；fail-open 不阻塞。

**提取置信度校准**（T140/D060）：来源基准 − 字段缺失扣分（金额/诊断 -0.25、
日期/姓名 -0.1）——缺金额 0.65 低于自动签发门 0.8、缺双核心 0.4 低于转人工门 0.6，
字段缺失直接影响调度。

**skill 装载**（`services/skills.py`）：`skills/<stage>/<line>.md` →
`skills/<stage>/_shared.md` → 代码内置 base prompt 三级回退。SOP 装配进 system
prompt，是人工调优面——准确率迭代改文本不改代码。

**Prompt 布局约束**（T165）：`build_system_prompt` 产出的 system 必须**全静态逐字
稳定**——同一 stage×line 内任何动态数据（案件快照 / 材料提取结果 / 理算事实）都不
进 system，一律走 user message + `<<<DATA…>>>DATA` 定界。原因见§13.3：DeepSeek
前缀缓存的分叉点决定命中率，动态数据插在中间会把它之后全部推出缓存窗口。
这条约束与 skill 文本的"可含任意大括号"约定不冲突（先 format 再拼 skill 的顺序
保留，只是不再对 base prompt 填动态占位符）。

**Worker 调用观测**（T164）：create_agent 子图内部 LLM 调用不经 `observed_ainvoke`，
经 `config.callbacks` 传播的 `LlmUsageCallbackHandler` 观测——langgraph 会把
callbacks 传播到节点内 chat model（真实模型实测，假模型探针因 tool_call 无限
循环不可用）。

## 6. 险种 pack

`schemas/lines.py`：一份 `InsuranceLinePack` = 一个险种的全部声明式知识——
受理分类关键词、必需材料单、条款要素（等待期/除外/限额）、责任认定兜底规则、
合法材料类型、除外同义词。

- 四线已上线：medical / auto / property / accident（T120，15 份险种 skill 规程）；
- 产品类型不在任何 pack（unknown）→ 受理期转人工（escape），不过管线；
- 上线新险种 = 新增 pack + skill 文本，节点零改动；
- `material_catalog()` 单源派生前端材料下拉（GET /api/v1/cases/material-catalog），
  新险种上线前端零改动（T126）。

**StageSpec 单源**（`schemas/stages.py`，D040）：六阶段的名字 / 结论 channel /
产出模型 / 前置条件 / 路由快照字段 / 回边 / prompt 清单由一份注册表派生——
守卫查表、图接线、评测断言、LLM 看到的目标清单全部同源，加阶段只改一处。

## 7. 合规与金额安全

- **规格契约**（`schemas/contract.py`）：自动签发线 / 等待期 / 调度预算 / 金额公式的
  唯一定义，运行时默认值、金样本期望、评测门阈值三方引用同一常量——评测门 =
  运行时门，不会漂移；
- **金额理算**（`services/amounts.py`）：纯确定性 `min(max(索赔 − 自费 − 免赔, 0) ×
  赔付比例, 保额)`，全链路 Decimal 精确到分；
- **决定书三层审查**（`services/decision_doc.py` + `nodes/compliance_gate.py`）：
  1. 骨架代码渲染：金额/结论/编号由代码注入结构化字段，LLM 只写核定依据叙述段
     （**零金额注入**——幻觉进不了金额字段）；
  2. 金额三方断言：正文提取金额 == decision 字段 == 理算结果，不一致 → MODIFY；
  3. `check_text` 红线检查（与评测门同一实现）：违规承诺话术 → REJECT 转人工；
- **修订闭环**：MODIFY → 规则叙述重渲染（version+1）→ 再过合规门，轮数上限
  （compliance_max_rounds）防死循环；
- **脱敏**：身份证 / 银行卡 / 手机号渲染层正则脱敏；坐席改判文本同样过 check_text。

## 8. HITL 人工介入

- **三类工单**：补件（supplement）/ 核赔复核（review）/ 升级（escape）；
- **挂起**：human_gate `interrupt` 挂起，工单列表由交付回执单源派生
  （`case_service.human_info_from_job`，D047——不读 checkpoint 猜状态，无 N+1）；
- **补件自动恢复**：客户补传材料 → 上传接口自动 `Command(resume)` → 材料审核重跑
  回调度（commit 先行防 SQLite 写锁）；
- **坐席结论必过合规**：签批（confirm）落坐席署名版本决定书；改判（rewrite）
  重渲染更高版本；坐席文本必过 check_text 红线复审，违规不签发；
- **叙述抽评**（T139/D059）：自动签发案件 5% 确定性采样（种子 = case_id 可复算）
  进抽评审队列，workbench 行内评审（pass / revise + 评语），通过率统计。

## 9. 在线客服域

T132-T137（D057）新增，与核赔主图**完全正交**的独立 ReAct Agent：

- **会话状态机**：ai → escalated → closed（closed 不可逆）；escalated 后 AI 停答
  （assistant 角色拒写），坐席接管；
- **客服 Agent**（`services/support/agent.py`）：create_agent + 会话记忆窗口 replay
  （store.recent_messages）；四工具——case_status_query（案件进度快照）、
  claim_draft_link（险种校验 + 报案链接四参数预填）、claim_rule_rag（条款问答）、
  escalate_to_human（转人工标记式：不改状态，AI 终局后确定性流转）；
- **门户侧**：chatui 全站悬浮客服气泡（SupportBubble），escalated 态 3s 轮询坐席
  回复，closed 终态只读 + 一键新会话；AI 回复中的报案链接可点；
- **坐席侧**：workbench /support 客服工单（transcript 三方气泡 / 回复 / 关闭备注）；
- **日期边界**：prompt 明确"不承诺赔付、不引内部阈值、禁止编造"。

## 10. 申请人记忆

`services/memory/case_memory.py`（T100 引入，T138 治理收口）：

- **写入**：案件终态（签发/拒赔/转人工等 4 处钩子）确定性渲染档案记录
  （结论/核定金额/原因），按案件幂等；fail-open 不阻塞主流程；
- **置信度门控**（D058）：auto 路径 `min(材料, 责任)` 置信度 < 0.6 不写只告警——
  低置信 not_covered 不进档案；
- **TTL**：应用层实现（expires_at + 惰性过滤，365 天可配）——InMemoryStore 不支持
  put(ttl=)，统一应用层防 dev/prod 行为漂移；
- **读取**：唯一人类消费点是 workbench 坐席"申请人核赔档案"卡（chatui 刻意不渲染，
  客户不应见跨案件档案）；路由注入开关默认关（T102 A/B 全量对比零增益证据）；
- **删除**：DELETE /api/v1/memory/{user_id}/entries/{case_id}（adelete + human
  审计）；非 tombstone——rebuild 脚本重跑会重建（D058 落档）。

## 11. 交付队列与案件生命周期

`services/case_jobs.py`（T103 引入，D044 混合方案）：

- **受理即返回**：POST /cases 建档 + 入队即返回，前端轮询终态（inline / background
  双档，小数据量直接 inline 驱动）；
- **常驻消费者循环**：JobLoop 认领 CaseJob 表任务执行图（T155 起每实例一循环、租约互斥）；`deliver_case_job` 三入口
  （提交/补件/工单处理）收口单一生命周期函数（D049）；
- **幂等**：自然键（user_id, policy_no, claimed_amount, incident_date）唯一约束，
  重复提交返回既有结论；
- **多实例（T155，D071）**：认领带租约（locked_by/lease_expires_at + 心跳续租），崩溃实例任务由租约到期接管；dev 多实例须 `CHECKPOINT_BACKEND=sqlite` 共享 checkpoint；
- **状态机**：received → 补件挂起 / 转人工挂起 / auto_issued / referred / closed…
  （`schemas/case.py` CaseStatus；终态集合单源）。

## 12. 数据模型

`services/db/models.py`，11 张表：

| 域 | 表 | 说明 |
|----|----|------|
| 核赔 | cases | 案件主表（事实态权威；自然键唯一） |
| 核赔 | case_events | 审计事件 append-only，（case_id, seq）唯一约束 |
| 核赔 | decision_documents | 决定书版本化 |
| 核赔 | case_jobs | 交付队列任务表 |
| 客服 | support_conversations / support_messages | 客服会话与三方时间线 |
| Mock | policies / medical_records / claim_records / kb_documents / eval_runs | 仿真种子数据与评测运行记录 |

LangGraph checkpoint / Store 表由 PostgreSQLSaver/Store 自管（D006，prod），
dev 用 InMemorySaver/InMemoryStore。JSONB 在 SQLite dev 自动降级 JSON，
同一套模型跑双后端。

## 13. 可观测性

### 13.1 三层埋点

| 层 | 实现 | 落点 | 粒度 |
|---|---|---|---|
| 指标 | `services/observability/metrics.py`（全量自定义指标定义） | Prometheus TSDB（保留 30d） | 聚合 |
| 追踪 | `services/observability/tracing.py`（OTel → Jaeger） | Jaeger（`:16686`） | 单次调用 |
| 日志 | `app/core/logging.py`（structlog） | stdout（**不落文件**） | 每事件 |

### 13.2 LLM 调用观测（T162-T165）

`llm_metrics.observed_ainvoke` 是全部 LLM 调用的统一入口，产出一条 `llm.<model>` span
+ 一次埋点：

- **Prompt 缓存指标** `LLM_CACHE_TOKENS{model, stage, result}`：DeepSeek 硬盘缓存
  默认开启（命中价 1/10），但命中情况此前是黑盒。
- **三来源提取**（`_extract_cache_usage`）——DeepSeek 用的是**非标字段**，
  langchain 的 `usage_metadata` 只映射标准字段会丢弃它们：
  1. `response_metadata["token_usage"]`——OpenAI SDK 原始 usage dict，
     `prompt_cache_hit_tokens`/`prompt_cache_miss_tokens` 在此存活（探针实测）；
  2. `usage_metadata["input_token_details"]["cache_read"]`——langchain 标准映射
     （换供应商通路，DeepSeek 不返回该标准字段）；
  3. 都没有 → 不记该维度（不臆造 0）。
- **结构化输出的特殊情况**：`with_structured_output` 返回**纯 Pydantic 对象**，
  不带 `usage_metadata`/`response_metadata`——usage 只能经
  `LlmUsageCallbackHandler.on_llm_end` 的 `llm_output` 取。因此 `observed_ainvoke`
  对「自身不带模型身份属性」的 Runnable 自动挂 handler，用量由 handler 记，
  主函数只补 span 属性，避免重复计数。
- **create_agent 子图**：worker_agent 装配的 CompiledStateGraph 内部 LLM 调用
  不经 `observed_ainvoke`，经 `config.callbacks` 传播的 handler 观测
  （实测 langgraph 会把 callbacks 传播到节点内 chat model）。
- **stage 维度**：`stage` 由 `token_tracker.current_phase()` 上下文携带
  （`phase_ainvoke` 标注）或 handler 显式传入。**这个维度是评估收益的前提**——
  没有它就无法区分「布局缺陷」与「天然 miss」（见§13.3）。

### 13.3 Prompt 布局与缓存命中率（T165）

DeepSeek 缓存按**前缀**命中：遇到第一个不同 token 即停止，其后全部 miss。
若动态数据（案件快照 / 材料提取结果 / 理算事实）写在 system 内，分叉点恰落在唯一
数据开头，命中率归零。改法是 `SystemMessage`（全静态）+ `HumanMessage`（动态数据
+ `<<<DATA…>>>DATA` 定界）双消息——顺带强化了注入防线（system=指令区、user=数据区）。

实测（真实 API，三轮不同快照）：

| 调用点 | 改造前 | 改造后 | 说明 |
|---|---|---|---|
| orchestrator | 0% | **86.8%** | 布局缺陷，已修 |
| liability_judge | — | 73~89% | system 本就纯静态 |
| material_review | 0% | **75~82%** | 布局缺陷，已修 |
| decision_writer | 0% | **59.5%** | 布局缺陷，已修 |
| ocr 材料提取 | 0% | 0% | **prompt 主体即唯一文档，天然 miss，非缺陷** |

整体（Grafana 实测）：**82.4%**。

### 13.4 其他

- 核赔业务指标：CASES_TOTAL{case_type, final_status}、CASE_STAGE_LATENCY{stage}、
  ROUTING_CALLS、GUARD_CORRECTIONS、ORCH_FALLBACK、CASE_TOKENS、DECISION_AMOUNT、
  CASE_JOBS_QUEUE_DEPTH（T154） 等；
- **Grafana**（T163/T164）：两个仪表盘共 **14 面板**——核赔 8 + 服务总览 6
  （含 4 个 Prompt 缓存面板）；
- **OTel采样率 0.2**（T166，此前 1.0 全采样）：全采样下每请求数十 span，
  磁盘与 CPU 均需付账；排障只需 trace_id 级定位单次调用；
- **Prometheus 保留期 30d**（T166）：v3 已把 TSDB 保留期移出配置文件
  （三种写法均报 `field not found in type config.plain`），
  只能走 `command: --storage.tsdb.retention.time=30d`；
- OTel 关闭时 noop tracer 零开销零侵入；
- 日志为 stdout JSON，**容器重建即丢**——需要事后回溯须另配 logging driver。

## 14. 评测体系

`evals/adjudication_suite.py`（编排）+ `evals/gates.py`（六门纯函数）+
`evals/adjudication_metrics.py`（判分）：

- **主门 153 案**（`datasets/adjudication.json`）：七类覆盖 × 四险种，
  期望金额按规格公式精确到分；确定性模式零 LLM，`--llm` 走真实调度；
- **对抗集拆两份**（T159，`--dataset adversarial` / `adversarial_holdout`）：

  | 数据集 | 规模 | 内容 | 门禁位置 |
  |---|---|---|---|
  | `adjudication_adversarial.json` | 8 | injection 8（注入/角色伪装/虚构免责/红线诱导/PII/施压/伪造/字段注入） | **push CI 硬门** |
  | `adjudication_adversarial_holdout.json` | 30 | robustness 6 seed + 24 红队变体 | **nightly 盲测** |

  拆分理由：同义词鲁棒性走关键词路径，**最易对fixture 过拟合**——留在 push CI 里
  会给"过了"一个假信号。`evals/redteam.py` 变异器分类法对齐 Garak promptinject /
  PyRIT converters-attacks，带 `framework_ref` 溯源（自研不引框架本体：探测对象
  语义错位 + 依赖重）；
- **六门**：金额 100% / 红线 0 / 守卫旁路 0 / 对抗全过（硬）+ 路由 ≥95% /
  责任 ≥90%（软）+ 调度 ≤15（预算）；阈值与运行时同源 `schemas/contract.py`；
- **客服问答门**（T146/D066，`support_suite.py` + `datasets/support_qa.json`
  15 案五类）：关键词组（组间 AND 组内 OR）/ 禁止词（含内部阈值泄漏）/ 转人工
  终态三层确定性判分，硬门 100%；检索命中率为分层观测（Agent 会改写检索词，
  原问题直检口径仅观测）。客服无确定性路径 → 需真 Key，本地手动门；CI 进
  其 schema 校验与判分单测；
- **单 Agent 消融**（T160，`evals/single_agent_baseline.py`）：同 153 案对比
  Orchestrator-Worker vs 单 Agent，量化证明拆分的必要性——

  | 项 | 多 Agent | 单 Agent |
  |---|---|---|
  | 一致率 | **88.9%**（136/153） | 60.8%（93/153） |
  | 金额硬门 | 失守 0 | **失守 88.9%** |
  | tokens/案 | 13.7k | 23.3k（+70%） |

  多 Agent 的 17 例失败全部为责任置信度 0.72< floor 0.8 按设计转人工（fail-safe）；
  单 Agent 双向失守：partial 理算 17/17 全灭 + 27 例错误 auto（无前置守卫/置信度门）
  + 12 例漏转补件；
- **RAG 评测**（T161，`evals/rag_metrics.py` + `scripts/eval_rag.py`）：
  责任认定阶段 QA 24 对（gold = source_file + 标记子串，单测逐字校验）：
  Recall@1=54% / @2=79% / @4=96% / @8=100%，MRR 0.724；
  RAGAS 四指标自实现（CP 0.868 / CR 0.806 / FA 0.929 / AR 0.909，deepseek 判分 +
  BGE-M3 反向问题嵌入，算法口径对齐官方定义）——自研而非引 `ragas` 包：
  dry-run 实测引入需 huggingface-hub 跨大版本漂移 + 20+ 新包，评估后维持自实现；
- **当前基线**：确定性 153/153 全绿、LLM 模式 153/153 全绿、注入门 8/8双模式全绿、
  hold-out 30/30 全绿、客服门 15/15、失败集 0；单测 **528 passed**；
- **CI 门禁**：eval-gate 跑确定性全量（零 secret）；docker job compose 全栈 +
  冒烟双档（无 secret → `--offline` 离线兜底档 22 项；有 secret → 完整档 23 项）
  + 多实例阶段（`--scale app=2`，T156）；nightly-llm 加 hold-out 盲测档。

## 15. 配置 / Profile / 部署

- **配置**：`app/core/config.py` pydantic-settings，全部从 `.env` 读取
  （LLM_API_KEY / LLM_MODEL / 核赔阈值组 / 记忆组 / 客服窗口…），不硬编码；
- **dev profile**：SQLite + Qdrant local mode + InMemorySaver/Store，零容器依赖；
- **prod profile**：PostgreSQL + Qdrant + Redis + AsyncPostgresSaver/Store，
  `docker compose up -d` 一键；
- **可选栈**：`--profile monitoring`（Prometheus + Grafana）、
  `--profile tracing`（OTel Collector + Jaeger）；
- **LLM**：OpenAI 兼容接口配置切换，当前 deepseek-flash（T130/D056，
  原生多模态兼 vision）；
- **CI**：ruff + pytest（528 用例）+ eval-gate + docker 冒烟（双档）+ 多实例阶段。

## 16. 关键决策索引

完整链见 [`.agent/decisions.md`](../.agent/decisions.md)，对架构影响最大的：

| 决策 | 一句话 |
|------|--------|
| D037/D039 | 产品转向核赔平台；LLM Orchestrator-Worker 全动态调度 |
| D038 | chatui 改造为案件提交门户 |
| D040/D041 | StageSpec registry 单源；架构评审候选落地（pack/契约/领域服务） |
| D042/D043/D055/D058 | 申请人记忆：写读治理三阶段，路由注入默认关（证据 T102） |
| D044 | 交付队列混合方案（单消费者 + SKIP LOCKED 升级位） |
| D046/D047/D049 | 决定书读模型 / 挂起信息 / 交付生命周期三次单源收口 |
| D052 | 死代码清除 + Worker 官方容错中间件对齐 |
| D053/D062 | 四险种一次上线；对抗集同义词清账 + 门禁升级 |
| D056/D063 | 模型切换 deepseek-flash；冒烟口径与两个真实缺陷修复 |
| D057 | 在线客服四决断（独立 Agent / 标记式转人工 / 轮询无 SSE） |
| D060/D061 | 置信度校准函数；checkpoint schema 版本门卫 |
| D064/D065 | 冒烟进 CI 双档（secret 可选，默认离线零依赖） |
| D066-D070 | Grafana 告警 / 负载测试 / 夜间 LLM 门 / 覆盖率基线（D070 批次） |
| D071 | 多实例横向扩展：租约队列 + dev 共享 SQLite checkpoint + 案件号计数行 |
| D072 | 双实例 live 冒烟脚本化进 CI（`--scale app=2` 阶段） |
| D073 | 红队变异器自研不引框架本体（Garak/PyRIT 探测对象语义错位 + 依赖重），分类法对齐 + `framework_ref` 溯源 |
| D074 | 单 Agent 消融口径（`--llm` 仅 LLM 调度，须 `--llm-workers` 补齐与生产同构） |
| D075 | RAGAS 四指标自实现（引包 dry-run 实锤 huggingface-hub 跨大版本漂移 + 20+ 新包） |
| D076 | Prompt 缓存修复分层：先 T164 观测补全拿基线，再 T165 布局改造；create_agent 子图用回调 handler 而非 ObservableChatModel 代理 |
