# claimflow 架构文档（核赔平台版）

> 本文记录系统当前的架构和实现。设计取舍见
> [`claimflow-新架构设计.md`](claimflow-新架构设计.md)，术语见 [`../CONTEXT.md`](../CONTEXT.md)，
> 全部技术决策链见 [`../.agent/decisions.md`](../.agent/decisions.md)（D001-D087）。

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
17. [已知限制与未来工作](#17-已知限制与未来工作)

---

## 1. 系统概览

客户提交理赔申请和材料后，LLM Orchestrator 调度六个 Worker：材料审核、
保单核验、风控筛查、责任认定、金额理算和决定书生成。

- 低风险小额案件自动签发《理赔决定书》；
- 其余转人工复核（补件 / 复核 / 升级三类工单，interrupt 挂起、补传自动恢复）；
- 各险种的知识放在 `skills/<stage>/<line>.md`。调整规程文本即可更新这部分知识。

有四条规则由代码保证：

1. 决定书生成后必须经过合规门。这条边写在图里，路由决策无法跳过。
2. 金额由 Decimal 和规格公式计算，决定书正文还要通过金额三方断言。
3. 守卫会修正违规路由；LLM 失败时走险种默认计划，并记录路由决策。
4. 自动签发的风险和金额阈值由 pydantic-settings 配置。

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

依赖从上往下。节点负责状态流转，业务规则放在服务层和工具层。

---

## 3. 核赔主图

`workflows/case_graph.py` 定义，`ClaimCaseState`（`state.py`）为共享状态。

受理节点 `nodes/intake.py` 优先按查到的保单 `product_type` 分类。保单查询返回
`None` 时，使用客户填写的 `declared_case_type`；两者都没有才标为 `unknown`。
未上线或无法识别的险种在受理阶段转人工。这里的回退只针对“查无保单”，
不代表保单查询抛异常时也会回退。

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

- Send 并行派发：orchestrator 的 `route_dispatch` 条件边返回 `[Send(state)]` 列表，
  一轮可并行投多个 worker（如 policy_verify ∥ fraud_check），区间重叠有测试断言；
- 回边派生：worker → orchestrator 的回边由 `StageSpec.back_to_orchestrator` 派生，
  不手写；
- interrupt 挂起：human_gate 用 LangGraph `interrupt` 挂起，checkpoint 持久化
  （thread_id = case_id），跨服务重启可恢复；
- checkpoint schema 版本（T141/D061）：`CASE_SCHEMA_VERSION` 版本戳写入 state；
  resume 时版本不匹配 → 删旧 thread + 用最近 RUN 原始输入全新重跑（案件事实权威在
  cases 表，重跑无损）+ `schema_reset` 审计事件；
- worker 阶段 span：`_timed` 为每个阶段生成含 case_id 的 OTel span。

## 4. LLM Orchestrator

`nodes/orchestrator.py`（调度装配）+ `nodes/guards.py`（确定性守卫纯函数层）。

每轮调度按以下顺序执行：

1. 按阶段快照字段组装当前案件上下文（材料结论 / 保单结论 / 风控结论…）；
2. LLM 结构化输出 `RoutingDecision{next[], plan, reason}`（function_calling 承载）；
3. `enforce_guards` 在代码层检查路由：
   - 前置条件缺失 → 改投首个 pending 前置阶段；
   - 重复派发去重；决定书生成必须单派；
   - 材料不齐时，`_COMPLETION_GATED` 阻止后续阶段执行；
   - 必做集未收敛 → 强制补投；
4. 调度调用超过 15 次或 LLM 失败时，按险种默认计划执行标准管线；
5. 守卫清空目标且未超预算 → 回落 `default_route`（D056-2 liveness 修复，
   防规划退化致案件中途终止）；
6. 路由决策（含守卫修正、模式、超预算原因）全量审计落 `case_events`。

注入防御（T131、T173）：路由 prompt 用 `<<<DATA … >>>DATA` 包住用户数据，
并在数据区之前说明数据不能作为指令。8 条注入用例中没有一次成功诱导路由。
后来又把同样的边界加到材料提取和责任认定：OCR 文本放在 user 消息中，
图片中的文字也按材料内容处理，责任认定的 facts 单独定界。红队测试新增
`material_embedding` 变异器，把指令放进材料提取字段，并纳入 nightly hold-out。

## 5. Worker 与 skill 作业规程

Worker 执行体（`services/worker_agent.py`）：`langchain.agents.create_agent`
子图 + 官方中间件栈：

- `ModelCallLimitMiddleware` 限制工具轮次；
- `ToolErrorMiddleware` 把工具异常转成 error ToolMessage，交给 Agent 处理（D052）；
- `GuardedTool`（工具层守卫）内层已带官方 `.with_retry()`，不重复叠加。

责任认定三段式（`nodes/liability_judge.py`，代表最深 worker）：

1. 确定性前置：保单无效 / 等待期未过（`出险日 > eff + N` 才过，D063 修 off-by-one）
   → 不经 LLM 直接 not_covered；
2. ReAct Agent：claim_rule_rag 条款检索 + diagnosis_matcher 诊断匹配，
   `LiabilityOutput` 结构化输出（条款引用 + 置信度）；
3. 关键词兜底：LLM 失败时险种 pack 的 exclusion_keywords 确定性判定
   （置信度 0.9，T142 六组同义词清账）。

`claim_rule_rag` 的在线调用链是 `nodes/liability_judge.py` →
`tools/claim/claim_rule_rag.py` → `services/rag/retriever.py`（Qdrant 向量片段）
和 `services/rag/graph_retriever.py`（图谱事实）。图谱由
`services/rag/knowledge_graph.py` 从 `data/graph/claim_rules_kg.json` 加载。
工具把向量片段放在 `results`，有图谱命中时另加 `graph_facts`，供责任认定 Agent
使用。`GRAPH_RAG_ENABLED` 默认开启；图文件缺失、实体未命中或图检索失败时，
这部分为空，向量结果仍可返回。图谱补的是结构化事实，不参与向量片段排序。

材料审核三段流水线（`nodes/material_review.py`）：

1. 逐份提取：图片/PDF/Word 两段式（文本提取 → 扫描件 vision），`@task` 子任务并行
   + 失败重试 + 崩溃恢复短路（T116）；
2. 规则层：险种必需材料完整性 + 发票/清单金额交叉核验（find_amount_contradictions）；
3. AI 一致性审查：结构化输出异常四类（姓名/诊断/金额/日期矛盾），通过必须空数组，
   中性观察进 notes 不压分（D063）；fail-open 不阻塞。

提取置信度校准（T140/D060）：从来源基准分扣除缺失字段分值。金额或诊断缺失
各扣 0.25，日期或姓名缺失各扣 0.1。缺金额时分数为 0.65，低于自动签发门槛 0.8；
两个核心字段都缺失时为 0.4，低于转人工门槛 0.6。

skill 装载（`services/skills.py`）：`skills/<stage>/<line>.md` →
`skills/<stage>/_shared.md` → 代码内置 base prompt 三级回退。SOP 装配进 system
prompt。规程内容可以直接改文本。

Prompt 布局约束（T165）：`build_system_prompt` 产出的 system 必须全静态逐字
稳定。同一 stage×line 内的案件快照、材料提取结果和理算事实都不
进 system，一律走 user message + `<<<DATA…>>>DATA` 定界。原因见§13.3：DeepSeek
前缀缓存的分叉点决定命中率，动态数据插在中间会把它之后全部推出缓存窗口。
这条约束与 skill 文本的"可含任意大括号"约定不冲突（先 format 再拼 skill 的顺序
保留，只是不再对 base prompt 填动态占位符）。

Worker 调用观测（T164）：create_agent 子图内部 LLM 调用不经 `observed_ainvoke`，
经 `config.callbacks` 传播的 `LlmUsageCallbackHandler` 观测。LangGraph 会把
callbacks 传播到节点内 chat model（真实模型实测，假模型探针因 tool_call 无限
循环不可用）。

## 6. 险种 pack

`schemas/lines.py` 中，每份 `InsuranceLinePack` 存放一个险种的声明式知识：
受理分类关键词、必需材料单、条款要素（等待期/除外/限额）、责任认定兜底规则、
合法材料类型、除外同义词。

- 四线已上线：medical / auto / property / accident（T120，20 份险种专属 skill 规程，另有 2 份共享规程）；
- 产品类型不在任何 pack（unknown）→ 受理期转人工（escape），不过管线；
- 上线新险种 = 新增 pack + skill 文本，节点零改动；
- `material_catalog()` 单源派生前端材料下拉（GET /api/v1/cases/material-catalog），
  新险种上线前端零改动（T126）。

`schemas/stages.py` 的 StageSpec 注册表记录六个阶段的名称、产出、前置条件、
快照字段、回边和 prompt。守卫、图接线、评测断言和 LLM 目标清单都从这里读取。

## 7. 合规与金额安全

- 规格契约（`schemas/contract.py`）：自动签发线 / 等待期 / 调度预算 / 金额公式的
  定义。运行时默认值、金样本期望和评测门阈值引用同一组常量；
- 金额理算（`services/amounts.py`）：纯确定性 `min(max(索赔 − 自费 − 免赔, 0) ×
  赔付比例, 保额)`，全链路 Decimal 精确到分；
- 决定书三层审查（`services/decision_doc.py` + `nodes/compliance_gate.py`）：
  1. 骨架代码渲染：金额、结论和编号由代码填写，LLM 只写核定依据叙述段；
  2. 金额三方断言：正文提取金额 == decision 字段 == 理算结果，不一致 → MODIFY；
  3. `check_text` 红线检查（与评测门同一实现）：违规承诺话术 → REJECT 转人工；
- 修订闭环：MODIFY → 规则叙述重渲染（version+1）→ 再过合规门，轮数上限
  （compliance_max_rounds）防死循环；
- 脱敏：身份证 / 银行卡 / 手机号渲染层正则脱敏；坐席改判文本同样过 check_text。
- PII 分层治理（T175-T176，D081-D082）：日志不记录原始 PII，身份证号在库内经
  确定性 AES 加密并由 ORM 透明解密，出站决定书继续脱敏。密钥从环境变量读取；
  旧明文行可读，错误密钥显式报错。

## 8. HITL 人工介入

- 三类工单：补件（supplement）/ 核赔复核（review）/ 升级（escape）；
- 挂起：human_gate 用 `interrupt` 挂起。`case_service.human_info_from_job` 从交付
  回执生成工单列表，不查询 checkpoint（D047）；
- 补件自动恢复：客户补传材料 → 上传接口自动 `Command(resume)` → 材料审核重跑
  回调度（commit 先行防 SQLite 写锁）；
- 坐席结论必过合规：签批（confirm）落坐席署名版本决定书；改判（rewrite）
  重渲染更高版本；坐席文本必过 check_text 红线复审，违规不签发；
- 叙述抽评（T139/D059）：自动签发案件 5% 确定性采样（种子 = case_id 可复算）
  进抽评审队列，workbench 行内评审（pass / revise + 评语），通过率统计。
- 门户与坐席访问（T171-T172，D077-D078）：客户案件接口使用案件访问令牌，
  坐席操作走 staff 鉴权；门户提交及查询有按配置启用的限流。

## 9. 在线客服域

在线客服由独立的 ReAct Agent 处理（T132-T137，D057），不进入核赔主图：

- 会话状态机：ai → escalated → closed（closed 不可逆）；escalated 后 AI 停答
  （assistant 角色拒写），坐席接管；
- 客服 Agent（`services/support/agent.py`）：create_agent + 会话记忆窗口 replay
  （store.recent_messages）；提供四个工具：case_status_query（案件进度快照）、
  claim_draft_link（险种校验 + 报案链接四参数预填）、claim_rule_rag（条款问答）、
  escalate_to_human（转人工标记式：不改状态，AI 终局后确定性流转）；
- 门户侧：chatui 全站悬浮客服气泡（SupportBubble），ai 态 SSE 流式渲染
  （T180/D085：token 逐段下发，done 后从服务端刷新完整时间线），
  escalated 态 3s 轮询坐席回复（有意取舍，D085），closed 终态只读 + 一键新会话；
  AI 回复中的报案链接可点；
- 坐席侧：workbench /support 客服工单（transcript 三方气泡 / 回复 / 关闭备注）；
- 日期边界：prompt 明确"不承诺赔付、不引内部阈值、禁止编造"。

## 10. 申请人记忆

`services/memory/case_memory.py`（T100 引入，T138 治理收口）：

- 写入：案件终态（签发/拒赔/转人工等 4 处钩子）确定性渲染档案记录
  （结论/核定金额/原因），按案件幂等；fail-open 不阻塞主流程；
- 置信度门控（D058）：auto 路径 `min(材料, 责任)` 置信度 < 0.6 时只告警，
  不写档案。低置信 not_covered 也不进档案；
- TTL：应用层用 expires_at 和惰性过滤实现，默认 365 天且可配置。InMemoryStore 不支持
  put(ttl=)，统一应用层防 dev/prod 行为漂移；
- 读取：唯一人类消费点是 workbench 坐席"申请人核赔档案"卡（chatui 刻意不渲染，
  客户不应见跨案件档案）；路由注入开关默认关（T102 A/B 全量对比零增益证据）；
- 删除：DELETE /api/v1/memory/{user_id}/entries/{case_id}（adelete + human
  审计）。删除不写 tombstone，重跑 rebuild 脚本会重新生成记录（D058）。

## 11. 交付队列与案件生命周期

`services/case_jobs.py`（T103 引入，D044 混合方案）：

- 受理即返回：POST /cases 建档 + 入队即返回，前端轮询终态（inline / background
  双档，小数据量直接 inline 驱动）；
- 常驻消费者循环：JobLoop 认领 CaseJob 表任务执行图（T155 起每实例一循环、租约互斥）；`deliver_case_job` 三入口
  （提交/补件/工单处理）收口单一生命周期函数（D049）；
- 幂等：自然键（user_id, policy_no, claimed_amount, incident_date）唯一约束，
  重复提交返回既有结论；
- 多实例（T155，D071）：认领带租约（locked_by/lease_expires_at + 心跳续租），崩溃实例任务由租约到期接管；dev 多实例须 `CHECKPOINT_BACKEND=sqlite` 共享 checkpoint；
- 状态机：received → 补件挂起 / 转人工挂起 / auto_issued / referred / closed…
  （`schemas/case.py` CaseStatus；终态集合单源）；
- 死信闭环（T182，D087）：重试耗尽 status=dead + job_failed 审计 → 发现
  （Gauge dead 列 + Grafana 第 4 条告警，dead>0 持续 5m）→ 处置
  （GET /interventions/jobs/dead 清单 + POST /interventions/jobs/{id}/redrive
  重驱：attempt 清零满额度、落 job_redrive 审计；replay_audit INV5 以
  job_redrive 事件放行重驱后的死信一致性）。
- 案号分配（T155，D071）：`case_id_counters` 按年计数，数据库原子自增；
  多实例并发提交共享同一流水号来源。

## 12. 数据模型

`services/db/models.py`，12 张表：

| 域 | 表 | 说明 |
|----|----|------|
| 核赔 | cases | 案件主表（事实态权威；自然键唯一） |
| 核赔 | case_events | 审计事件 append-only，（case_id, seq）唯一约束；五不变量回放断言工具 `scripts/replay_audit.py`（T181/D086） |
| 核赔 | decision_documents | 决定书版本化 |
| 核赔 | case_jobs | 交付队列任务表 |
| 核赔 | case_id_counters | 按年原子分配案件号 |
| 客服 | support_conversations / support_messages | 客服会话与三方时间线 |
| Mock | policies / medical_records / claim_records / kb_documents / eval_runs | 仿真种子数据与评测运行记录 |

LangGraph checkpoint / Store 表由 AsyncPostgresSaver/Store 自管（D006，prod），
dev 用 InMemorySaver/InMemoryStore。JSONB 在 SQLite dev 自动降级 JSON，
同一套模型跑双后端。

## 13. 可观测性

### 13.1 三层埋点

| 层 | 实现 | 落点 | 粒度 |
|---|---|---|---|
| 指标 | `services/observability/metrics.py`（全量自定义指标定义） | Prometheus TSDB（保留 30d） | 聚合 |
| 追踪 | `services/observability/tracing.py`（OTel → Jaeger） | Jaeger（`:16686`） | 单次调用 |
| 日志 | `app/core/logging.py`（structlog） | stdout（不落文件） | 每事件 |

### 13.2 LLM 调用观测（T162-T165）

`llm_metrics.observed_ainvoke` 覆盖直接 LLM 调用；create_agent 子图通过 callback
观测内部调用。每次调用产出一条 `llm.<model>` span 和一次埋点：

- Prompt 缓存指标 `LLM_CACHE_TOKENS{model, stage, result}`：DeepSeek 硬盘缓存
  默认开启（命中价 1/10），但命中情况此前是黑盒。
- 币值指标（T177，D083）`LLM_COST_CNY{model, stage}`：`record_llm_call` 内按
  单价表（`LLM_PRICING="model:miss,hit,completion;..."` ¥/M，配置驱动）折算：
  `(prompt−hit)×miss价 + hit×hit价 + completion×completion价`；hit 未知全按 miss
  （上界口径），单价表缺模型不记（不臆造）。"单案成本"经 PromQL 派生
  `increase(cost)/increase(cases)`（分母随时间窗变化，不在应用内算）；
  Grafana 核赔板"单案 Token 均值 / 单案 LLM 成本估算（¥）"两面板，评测报告
  cost 块含 `cost_cny_total/per_case`（registry 差分）。
- 缓存用量提取（`_extract_cache_usage`）：DeepSeek 使用非标准字段，
  langchain 的 `usage_metadata` 只映射标准字段会丢弃它们：
  1. 先读 `response_metadata["token_usage"]` 中的 OpenAI SDK 原始 usage，
     `prompt_cache_hit_tokens`/`prompt_cache_miss_tokens` 在此存活（探针实测）；
  2. 再读 `usage_metadata["input_token_details"]["cache_read"]` 中的 LangChain 标准映射
     （换供应商通路，DeepSeek 不返回该标准字段）；
  3. 都没有 → 不记该维度（不臆造 0）。
- 结构化输出的特殊情况：`with_structured_output` 返回纯 Pydantic 对象，
  不带 `usage_metadata`/`response_metadata`。usage 只能经
  `LlmUsageCallbackHandler.on_llm_end` 的 `llm_output` 取。因此 `observed_ainvoke`
  对「自身不带模型身份属性」的 Runnable 自动挂 handler，用量由 handler 记，
  主函数只补 span 属性，避免重复计数。
- create_agent 子图：worker_agent 装配的 CompiledStateGraph 内部 LLM 调用
  不经 `observed_ainvoke`，经 `config.callbacks` 传播的 handler 观测
  （实测 langgraph 会把 callbacks 传播到节点内 chat model）。
- stage 维度：`stage` 由 `token_tracker.current_phase()` 上下文携带
  （`phase_ainvoke` 标注）或 handler 显式传入。按 stage 分开后，可以区分
  「布局缺陷」和「天然 miss」（见§13.3）。

### 13.3 Prompt 布局与缓存命中率（T165）

DeepSeek 缓存按前缀命中：遇到第一个不同 token 即停止，其后全部 miss。
若动态数据（案件快照 / 材料提取结果 / 理算事实）写在 system 内，分叉点恰落在唯一
数据开头，命中率归零。改法是 `SystemMessage`（全静态）+ `HumanMessage`（动态数据
+ `<<<DATA…>>>DATA` 定界）双消息。system 放指令，user 放案件数据。

实测（真实 API，三轮不同快照）：

| 调用点 | 改造后 | 说明 |
|---|---|---|
| orchestrator | 86.7%–86.8% | 三轮不同快照实测；改造前缺同口径观测，不以 0% 作基线 |
| liability_judge | 73%–89% | system 本就纯静态 |
| material_review | 75%–82% | 静态规程与动态数据分离 |
| decision_writer | 59.5% | 静态规程与动态数据分离 |
| ocr 材料提取 | 0% | prompt 主体是每案唯一文档，天然 miss |

整体（Grafana 实测）：82.4%。30 案 LLM 评测按配置单价表估算约
¥0.006/案（无缓存约 ¥0.016/案）；这是估算口径，不是账单金额。

### 13.4 其他

- 核赔业务指标：CASES_TOTAL{case_type, final_status}、CASE_STAGE_LATENCY{stage}、
  ROUTING_CALLS、GUARD_CORRECTIONS、ORCH_FALLBACK、CASE_TOKENS、DECISION_AMOUNT、
  CASE_JOBS_QUEUE_DEPTH（T154） 等；
- Grafana（T163/T164/T177/T179）：两个仪表盘共 22 个面板。核赔板 10 个，
  包括单案 Token 和成本；服务总览板 12 个，包括 Prompt 缓存和工具缓存。
  T179 清理了四个引用旧指标的面板，转人工率改用 CASES_TOTAL 的终态计算；
- OTel 采样率 0.2（T166，此前为 1.0）：全采样时，每个请求会产生数十个 span，
  增加磁盘和 CPU 开销；
- Prometheus 保留期 30d（T166）：v3 已把 TSDB 保留期移出配置文件
  （三种写法均报 `field not found in type config.plain`），
  只能走 `command: --storage.tsdb.retention.time=30d`；
- OTel 关闭时 noop tracer 零开销零侵入；
- 日志写到 stdout，格式为 JSON。需要跨容器重建保留日志时，须另配 logging driver。

## 14. 评测体系

`evals/adjudication_suite.py`（编排）+ `evals/gates.py`（六门纯函数）+
`evals/adjudication_metrics.py`（判分）：

- 主门 153 案（`datasets/adjudication.json`）：七类覆盖 × 四险种，
  期望金额按规格公式精确到分；确定性模式零 LLM，`--llm` 走真实调度；
- 对抗集拆两份（T159，`--dataset adversarial` / `adversarial_holdout`）：

  | 数据集 | 规模 | 内容 | 门禁位置 |
  |---|---|---|---|
  | `adjudication_adversarial.json` | 8 | injection 8（注入/角色伪装/虚构免责/红线诱导/PII/施压/伪造/字段注入） | push CI 硬门 |
  | `adjudication_adversarial_holdout.json` | 30 | robustness 6 seed + 24 红队变体 | nightly 盲测 |

  同义词鲁棒性走关键词路径，容易对 fixture 过拟合，所以放在 nightly 盲测。
  `evals/redteam.py` 变异器分类法对齐 Garak promptinject /
  PyRIT converters-attacks，带 `framework_ref` 溯源（自研不引框架本体：探测对象
  语义错位 + 依赖重）；
- 六门：金额 100% / 红线 0 / 守卫旁路 0 / 对抗全过（硬）+ 路由 ≥95% /
  责任 ≥90%（软）+ 调度 ≤15（预算）；阈值与运行时同源 `schemas/contract.py`；
- 客服问答门（T146/D066，`support_suite.py` + `datasets/support_qa.json`
  15 案五类）：关键词组（组间 AND 组内 OR）/ 禁止词（含内部阈值泄漏）/ 转人工
  终态三层确定性判分，硬门 100%；检索命中率为分层观测（Agent 会改写检索词，
  原问题直检口径仅观测）。客服无确定性路径 → 需真 Key，本地手动门；CI 进
  其 schema 校验与判分单测；
- 单 Agent 消融（T160，`evals/single_agent_baseline.py`）：用同一批 153 案
  对比 Orchestrator-Worker 和单 Agent：

  | 项 | 多 Agent | 单 Agent |
  |---|---|---|
  | 一致率 | 88.9%（136/153） | 60.8%（93/153） |
  | 金额硬门 | 失守 0 | 失守 88.9% |
  | tokens/案 | 13.7k | 23.3k（+70%） |

  多 Agent 的 17 例失败全部为责任置信度 0.72< floor 0.8 按设计转人工（fail-safe）；
  单 Agent 双向失守：partial 理算 17/17 全灭 + 27 例错误 auto（无前置守卫/置信度门）
  + 12 例漏转补件；
- RAG 评测（T161，`evals/rag_metrics.py` + `scripts/eval_rag.py`）：
  责任认定阶段 QA 24 对（gold = source_file + 标记子串，单测逐字校验）。
  以下召回指标只测基础向量检索，不含重排和图谱事实：
  Recall@1=54% / @2=79% / @4=96% / @8=100%，MRR 0.724；
  RAGAS 四指标自实现（CP 0.868 / CR 0.806 / FA 0.929 / AR 0.909，deepseek 判分 +
  BGE-M3 反向问题嵌入，算法口径对齐官方定义）。没有引入 `ragas` 包：
  dry-run 实测引入需 huggingface-hub 跨大版本漂移 + 20+ 新包，评估后维持自实现；
- 当前基线：CI 确定性档 153/153 全绿；T130 的 LLM 调度档
  （真实调度、worker 确定性）153/153、六门全绿。T160 全程调用模型档
  （`--llm --llm-workers`）一致率 88.9%（136/153），硬门全绿，路由软门未到
  95%；注入门 8/8 双模式全绿，hold-out 30/30，客服门 15/15；
  `uv run pytest -q` 628 passed（2026-11-02 复测）；
- CI 门禁：eval-gate 跑确定性全量（零 secret）；docker job compose 全栈 +
  冒烟双档（无 secret → `--offline` 离线兜底档 22 项；有 secret → 完整档 23 项）
  + 多实例阶段（`--scale app=2`，T156）；nightly-llm 加 hold-out 盲测档。

## 15. 配置 / Profile / 部署

- 配置：`app/core/config.py` pydantic-settings，全部从 `.env` 读取
  （LLM_API_KEY / LLM_MODEL / 核赔阈值组 / 记忆组 / 客服窗口…），不硬编码；
- dev profile：SQLite + Qdrant local mode + InMemorySaver/Store，零容器依赖；
- prod profile：PostgreSQL + Qdrant + Redis + AsyncPostgresSaver/Store，
  `docker compose up -d` 一键；
- 可选栈：`--profile monitoring`（Prometheus + Grafana）、
  `--profile tracing`（OTel Collector + Jaeger）；
- LLM：OpenAI 兼容接口配置切换，当前 deepseek-flash（T130/D056，
  原生多模态兼 vision）；
- CI：lint-test、frontend、docker、eval-gate、nightly-llm 五个 job；
  pytest 基线 628 passed，覆盖率门 ≥85%；docker job 含双档冒烟和双副本阶段。

## 16. 关键决策索引

完整链见 [`.agent/decisions.md`](../.agent/decisions.md)，对架构影响最大的：

| 决策 | 一句话 |
|------|--------|
| D037/D039 | 产品转向核赔平台；LLM Orchestrator-Worker 全动态调度 |
| D038 | chatui 改造为案件提交门户 |
| D040/D041 | StageSpec registry 单源；架构评审候选落地（pack/契约/领域服务） |
| D042/D043/D055/D058 | 申请人记忆：写读治理三阶段，路由注入默认关（证据 T102） |
| D044/D071 | 交付队列混合方案；T155 起每实例一消费者，租约认领互斥，SKIP LOCKED 保留升级位 |
| D046/D047/D049 | 决定书读模型 / 挂起信息 / 交付生命周期三次单源收口 |
| D052 | 死代码清除 + Worker 官方容错中间件对齐 |
| D053/D062 | 四险种一次上线；对抗集同义词清账 + 门禁升级 |
| D056/D063 | 模型切换 deepseek-flash；冒烟口径与两个真实缺陷修复 |
| D057 | 在线客服四决断（独立 Agent / 标记式转人工 / v1 轮询交互，AI 态流式后由 D085 兑现） |
| D060/D061 | 置信度校准函数；checkpoint schema 版本门卫 |
| D064/D065 | 冒烟进 CI 双档（secret 可选，默认离线零依赖） |
| D066-D070 | Grafana 告警 / 负载测试 / 夜间 LLM 门 / 覆盖率基线（D070 批次） |
| D071 | 多实例横向扩展：租约队列 + dev 共享 SQLite checkpoint + 案件号计数行 |
| D072 | 双实例 live 冒烟脚本化进 CI（`--scale app=2` 阶段） |
| D073 | 红队变异器自研不引框架本体（Garak/PyRIT 探测对象语义错位 + 依赖重），分类法对齐 + `framework_ref` 溯源 |
| D074 | 单 Agent 消融口径（`--llm` 仅 LLM 调度，须 `--llm-workers` 补齐与生产同构） |
| D075 | RAGAS 四指标自实现；依赖试装发现 huggingface-hub 跨大版本漂移，且新增 20 多个包 |
| D076 | Prompt 缓存修复分层：先 T164 观测补全拿基线，再 T165 布局改造；create_agent 子图用回调 handler 而非 ObservableChatModel 代理 |
| D077-D078 | 案件门户访问令牌与限流：客户访问按案件授权，限流在 prod 用 Redis Lua 原子实现 |
| D079-D080 | 材料提取与责任认定补齐注入数据定界；坐席改判经人工 triage 回流独立回归集 |
| D081-D082 | PII 分层治理与身份证号库内确定性加密 |
| D083-D084 | LLM 单价表驱动的单案成本估算；叙述 judge 与人工抽评对账，仅作观测 |
| D085 | 客服 SSE 流式：AI 应答流式，escalated 坐席回复与案件进度保留轮询（有意取舍）；POST + ReadableStream 而非 EventSource-GET |
| D086 | 轨迹回放断言：事件流 × 回执表 × 决定书版本联合断言五不变量（非金轨迹比对），schema_reset 段边界与补件轮次配对 |
| D087 | 死信重驱三件套：dead 列告警 + staff 重驱端点（attempt 清零/last_error 保留）+ INV5 以 job_redrive 放行；不做批量/自动重驱 |

---

## 17. 已知限制与未来工作

> T179 集中收录（此前散落各节）；本节是"反思 / 重做会怎么改"素材的单点入口，
新限制先落这里再考虑是否单开任务。

### 已知限制

运行与运维：

- T181 在 dev 历史库查出 10 案、11 条审计异常：9 案有 seq 重号或缺失，
  1 案缺首条路由事件，1 案在 review 状态违规补件。其中一案有两类异常。
  旧库缺少 `(case_id, seq)` 物理唯一约束；现行代码已加约束并使用 T095 的单分配器。
  历史事件保留原样，回放工具仍会报告这些异常。
- 日志只写 stdout，不持久保存。事后回溯需要另配 logging driver。
- dev 多实例下记忆 store 与工具缓存仍进程内（T155 已知边界，prod 共享
  无此问题）；dev 多实例另须显式 `CHECKPOINT_BACKEND=sqlite`（T145 验证）
- 交付队列每实例一消费者、租约互斥（T155/D071）：SKIP LOCKED 升级判据已落档未实施
  （日案件量 >1 万或积压排空 >10 分钟才升级，当前量级远未触发）

LLM 与缓存：

- ocr 材料提取缓存 0% 命中是天然 miss（prompt 主体即唯一文档内容），
  T165 布局改造只对"静态规程+动态数据"形态的调用点有效
- 客服域无确定性路径 → 客服门需真 Key 本地手动跑，CI 只进 schema 校验与
  判分单测；客服检索命中率为分层观测口径（Agent 会改写检索词）
- PII 库内加密（D082）要轮换密钥，还需要 v2 双写迁移。确定性加密保留了
  同值可比较的特性，供证件号匹配使用。

评测与质量：

- RAG Recall@1 为 54%。唯一一次 top-4 未命中与 gold 只指定单篇文档有关：
  潜水免责的答案同时出现在两篇文档中。
- badcase 回归集（T174/D080）与 judge 对账（T178/D084）依赖真实坐席操作
  积累样本，当前量级尚小；回归集未挂 nightly（可挂）
- 叙述 judge 温度 0 单次判分仍有方差（RAGAS 同款口径），一致率按案比较容忍

交互：

- 客服 AI 应答已 SSE 流式（T180/D085）；escalated 坐席回复 3s 轮询与案件进度
  GET 轮询为有意取舍（不定时/分钟级事件，维持长连接成本高于收益，D085）
- 用户只说"9月20日"时模型自行补年份（T137 观察项，prompt 已补推断规则）

### 未来工作候选（按价值密度排序）

1. 模型路由实验（证据驱动）：客服域或叙述生成换小模型 A/B，六门+成本
   双口径对比，结论无论正负落 decisions.md
2. 回归集挂 nightly：`--dataset regression` 进 nightly-llm job（样本
   积累后）
3. replay_audit 挂 nightly：退出码已备好，样本积累后与回归集同挂（当前
   手动跑，T181 已在 dev 库 316 案实测 306 过 / 10 案历史异常）
