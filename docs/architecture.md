# claimflow 架构文档（核赔平台版）

> 本文是保险理赔智能核赔平台的架构总览，与工程版设计文档
> [`claimflow-新架构设计.md`](claimflow-新架构设计.md) 互补：设计文档讲"为什么这样设计"，
> 本文讲"现在系统长什么样"。统一语言见 [`../CONTEXT.md`](../CONTEXT.md)，
> 全部技术决策链见 [`../.agent/decisions.md`](../.agent/decisions.md)（D001-D065）。

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
- **常驻单消费者**：JobLoop 认领 CaseJob 表任务执行图；`deliver_case_job` 三入口
  （提交/补件/工单处理）收口单一生命周期函数（D049）；
- **幂等**：自然键（user_id, policy_no, claimed_amount, incident_date）唯一约束，
  重复提交返回既有结论；
- **单实例约束**：当前 replicas=1；多实例需补 SKIP LOCKED 租约认领（D044 留位）；
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

- **Prometheus 指标**（`services/observability/metrics.py`）：核赔业务组——
  CASES_TOTAL{case_type, final_status}（自动结案率/转人工率推导）、
  CASE_STAGE_LATENCY{stage}、ROUTING_CALLS、GUARD_CORRECTIONS、ORCH_FALLBACK、
  CASE_TOKENS（7,075.8/案实测）、DECISION_AMOUNT 等；
- **Grafana**：`grafana/claimflow-adjudication.json` 8 面板（自动结案率 / 转人工率 /
  调度调用 / 调度健康 / 阶段 P95 / 端到端 / 案件量 / 核定金额）；
- **OTel/Jaeger**（T039 + T128）：trace_id 贯穿交付 → 阶段 → LLM 三层 span；
  OTel 关闭时 noop tracer 零开销；
- **结构化日志**：structlog JSON。

## 14. 评测体系

`evals/adjudication_suite.py`（编排）+ `evals/gates.py`（六门纯函数）+
`evals/adjudication_metrics.py`（判分）：

- **主门 153 案**（`datasets/adjudication.json`）：七类覆盖 × 四险种，
  期望金额按规格公式精确到分；确定性模式零 LLM，`--llm` 走真实调度；
- **对抗门 14 案**（`datasets/adjudication_adversarial.json`，`--dataset adversarial`）：
  injection 8（注入/角色伪装/虚构免责/红线诱导/PII 诱导/施压翻转/事实伪造/字段注入）
  + robustness 6（除外同义词），双 tier 均为硬门（T142 升级）；
- **六门**：金额 100% / 红线 0 / 守卫旁路 0 / 对抗全过（硬）+ 路由 ≥95% /
  责任 ≥90%（软）+ 调度 ≤15（预算）；阈值与运行时同源 `schemas/contract.py`；
- **当前基线**：确定性 153/153 全绿、LLM 模式 153/153 全绿、对抗门 14/14 双模式
  全绿、失败集 0；
- **CI 门禁**：eval-gate 跑确定性全量（零 secret）；docker job compose 全栈 +
  冒烟双档（无 secret → `--offline` 离线兜底档 22 项；有 secret → 完整档 23 项）。

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
- **CI**：ruff + pytest（444 用例）+ eval-gate + docker 冒烟（双档）。

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
