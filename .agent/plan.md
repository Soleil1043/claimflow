# 设计说明书（Plan）——保险理赔智能核赔平台

> Phase 2 产出（2026-09-04，D037-D039）。**主体架构以 docs/claimflow-新架构设计.md 为准**
> （本文件不重复架构细节，只记录落地映射、选型增量与风险）。
> 后续重大设计变动见 `decisions.md` D071-D076；对应 Prompt 见 `prompts.md` 的「设计变更 Prompt」。
> 状态标记：✅ 已确认 | 🔄 待确认 | ❌ 需修改

**状态**：✅ 已确认（D037-D039 方向决策已落地；T168 按 sdd-scaffold 模板补齐结构说明）

---

## 0. 本文件与架构文档的分工

模板约定 plan.md 含技术选型 / 目录结构 / 数据模型 / API / 第三方依赖 / 关键技术决策六节。
本项目把这部分**外置**到 `docs/`（避免同一信息两处维护而漂移）：

| 模板节 | 本项目落在|
|---|---|
| 技术选型 | 本文 §1（增量口径）+ `AGENTS.md` §2（完整栈） |
| 目录结构 | `AGENTS.md` §5（文件结构约定） |
| 数据模型 | `docs/architecture.md` §12 + `schemas/`（`api.py`/`case.py`/`db/models.py`） |
| API 设计 | `README.md` §8 接口表 + `app/api/v1/` + `schemas/api.py` |
| 第三方依赖 | 本文 §1（关键依赖与口径） |
| 关键技术决策 | 本文 §6 + `decisions.md` D001-D076 |

---

## 1. 技术选型（增量）

基础栈沿用 AGENTS.md 第 3 节（LangGraph / FastAPI / PostgreSQL / Qdrant / Redis /
BGE-M3 / uv / pytest / Docker / Prometheus+Grafana），增量与关键口径：

| 项 | 选择 | 依据 |
|---|---|---|
| 编排形态 | orchestrator 循环：`RoutingDecision` 结构化输出 + `Command(goto=[Send...])` 并行 + 静态合规门 | D039 |
| 调度/worker LLM | deepseek-v4-flash（主链路），vision-exp 专职 OCR | D002/D007/D008 |
| skill 机制 | `skills/<stage>/<line>.md` + `services/skills.py` 装载器（纯文本零依赖） | D039 |
| Checkpoint | AsyncPostgresSaver（prod）/ InMemorySaver（dev），thread_id=case_id | D009 |
| 业务表 | cases / case_events（含 kind=routing 审计）/ decision_documents / human_tickets | D006 职责分离原则延续 |
| 金额 | 全链路 Decimal，序列化 str/分 | F08 硬门前提 |
| 结构化输出 | with_structured_output(method="function_calling") | D022/T068 实测口径 |

## 2. 架构要点索引（详见架构设计文档）

- 第四节：orchestrator 设计（守卫表/失败兜底/决策审计/图骨架）——**本方案核心**
- 第五节：ClaimCaseState 分域 + 阶段 Pydantic 模型 + 字段所有权表（并行安全）
- 第八节：HITL 三类工单（interrupt + Command(resume)，移植 T037）
- 第九节：checkpoint 与业务表职责分离 + 幂等
- 第十节：静态合规门 + 金额一致性断言
- 第七/十三节：工具映射与目录结构（含 skills/）

## 3. 里程碑与任务映射（已全部交付）

| 里程碑 | 任务 | 门禁摘要 |
|---|---|---|
| M0 立项 | T077 | spec/plan/tasks/设计文档 就绪 |
| M1 骨架 | T078 域模型 → T079 主图骨架（兜底编排+守卫）→ T080 案件 API | 20 金样本（桩工具）金额/路由断言全绿 |
| M2 LLM 化 | T081 skill+orchestrator → T082 材料 → T083 保单/风控 → T084 责任 → T085 决定书+合规门 | 守卫注入 100% 拦截；金额注入 100% 拦截 |
| M3 HITL | T086 interrupt 全链 → T087 工作台 | 补件/签批/跨重启恢复 e2e |
| M4 评测观测 | T088 金样本判分 → T089 上线门 → T090 埋点 | 硬门全绿 + 路由 ≥95% + 调用 ≤15 |
| M5 收尾 | T091 演示门户 → T092 容器化 → T093 清理收尾 | 全量回归绿 |

依赖链严格串行（工作流约束：不跳依赖、不同时多任务）。

## 4. 风险与对策

| 风险 | 对策 |
|---|---|
| orchestrator 绕圈/失控 | recursion_limit + ModelCallLimit + 守卫必做集强制收敛 + routing_calls_per_case 指标（预算 15） |
| orchestrator 路由错误 | 前置条件守卫（代码层）+ 失败走险种默认计划 + 金样本路由判分（软门 ≥95%） |
| 合规被绕过 | decision_generate→compliance_gate 静态边（图结构断言进 T085 测试） |
| LLM 幻觉金额 | 理算纯确定性工具 + 决定书金额一致性断言（不依赖 LLM） |
| 金额精度 | 全链路 Decimal，序列化 str/分 |
| 多险种 mock 数据缺失 | pack 分批；未上线险种受理转人工；首批只做医疗险 |
| 金样本标注成本 | 程序化生成 + 人工校验（T026/T065 经验） |
| skill 改动回归风险 | skill 变更必须跑金样本回归；接 A/B 框架对比（skill_off/on 变体） |

---

## 6. 维护期重构：结构精简与官方容错对齐（2026-09-11，D052，T112-T117）

> 依据：全库结构审阅 + Docs by LangChain 官方文档逐项核实。任务清单见 tasks.md 增量节。

### 6.1 删除面（P0，零行为变化）

| 对象 | 说明 |
|---|---|
| evals v1 残骸 | metrics/trajectory/judge 三件 + schemas Eval* 三类 + 对应测试（运行器 test_suite 已随 T094 删，此为残余） |
| long_term 会话摘要半边 | 保留 Store 管线（get_memory_store / _ensure_pg_setup / search_store_items）；删 LLM 摘要路径（现行申请人记忆 case_memory 为确定性渲染，零 LLM）+ MEMORY_SUMMARY_PROMPT + verify_memory.py |
| 零散死码 | schemas/agent.py（零消费者）、ToolOutput（零代码消费者）、MAX_HISTORY_MESSAGES、demo_hitl_backend.py（调已删 conversations API）、verify_ui.py（import 已删 ui/，运行即 ImportError） |
| v1 配置项 | memory_summary_every_n_turns / memory_top_k / memory_min_score / turn_token_budget（消费者均在删除面内） |
| token_tracker 轮次语义 | start/finish_turn_tokens 仅测试调用，现行入口 track_case（案件维度）；TURN_TOKENS 指标文案同步 |

预期净删约 1500 行。

### 6.2 官方对齐面（P1）

1. **Worker 子图容错**：`ToolRetryMiddleware(on_failure="error")`（内层）+ `ToolErrorMiddleware`
   （外层）装配进 worker_agent 的 create_agent middleware（与 ModelCallLimitMiddleware 并列）。
   语义链：工具系统异常 → error ToolMessage（LLM 可见可自愈）→ 重试耗尽 → 既有确定性兜底。
2. **材料提取 @task 子任务化**：material_review 节点内逐份提取包 `@task(retry_policy=, timeout=)`；
   三收益——崩溃恢复跳过已提取材料（不重付 LLM）、声明式容错、任务粒度 trace；串行 for 顺带
   改 future 并行。注意 ChatOpenAI max_retries=1 与 RetryPolicy 的叠加倍数（超时预算放大）。

### 6.3 明确不动（防过度工程，官方核实无对应物或语义不同）

CircuitBreaker（官方无熔断）、ToolResultCache Redis 版（cache_policy 语义不同）、渲染层脱敏
（官方 PII middleware 层次不同）、orchestrator / 材料 AI 审查的快速兜底（D039 设计）。

### 6.4 验证口径

每任务全量 pytest + ruff；T115/T116 配负向用例（工具异常自愈 / 恢复短路）；删除类任务要求
金样本评测门确定性模式指标零变化。

---

## 7. P1 工程完善批次（2026-09-24 追加，来源：完善度评估 P1 清单，用户指令执行）

> 背景：P0 三项（坐席鉴权 T147 / 前端 CI T148 / 覆盖率阈值 T149）已收口。
> 本批次补齐生产化差距，任务 T150-T154，每任务独立 commit + 回归验证。

### 7.1 T150 负载测试（量化单消费者队列边界）

- **目标**：量化交付队列（D044 单消费者）的吞吐上限、积压深度与端到端延迟分布，
  为 SKIP LOCKED 多实例升级位提供触发证据（"什么负载下必须升"有数字）。
- **做法**：`scripts/load_test.py`（httpx asyncio 并发，不引 locust 新依赖）——
  N 案件并发提交（受理即返回路径）+ 逐案轮询终态；确定性编排
  （ORCHESTRATOR_LLM_ENABLED=false）+ 零材料（挂起 supplement_pending 即终态，
  走完 intake + 材料审核规则层 + 交付队列真实消费，零 LLM 零成本）。
- **指标**：提交吞吐（RPS）/ 交付吞吐（案件/分钟）/ 峰值积压（pending 深度）/
  端到端延迟 P50/P95 / 轮询 QPS。
- **产出**：报告落 `evals/reports/`（t150_load_test.json）+ 结论写 decisions.md。

### 7.2 T151 真实 LLM 门进 CI 夜间任务

- **目标**：客服门 15 案 / 对抗门 LLM 档 14 案 / 主门 LLM 档抽样从"人记得跑"
  变"每日自动跑"（回归靠日历不靠记性）。
- **做法**：ci.yml 加 `nightly-llm` job（`schedule: cron` + workflow_dispatch 手动
  触发口）；无 LLM_API_KEY secret 时 skip（fork 场景）；HF 模型走 actions/cache
  （T072 先例），ingest 重建向量索引（data/qdrant 不入库）；三档顺序跑，
  任一失败 exit 1。
- **验收**：YAML 解析校验 + 本地等价命令可跑（真实跑等 push 后 GitHub 首夜验证）。

### 7.3 T152 依赖漏洞审计进 CI

- **后端**：lint-test job 加 `uv run pip-audit`（pyproject dev 依赖加 pip-audit）。
- **前端**：frontend job 加 `npm audit --audit-level=high`（exit 非 0 才红）。
- **验收**：本地 pip-audit / npm audit 跑通（如现有依赖有已知漏洞，如实记录并
  评估升级，不为绿而忽略）。

### 7.4 T153 超长文件拆分（有职责边界的两个）

- **范围**：只拆有明确职责边界的两个——
  `services/case_jobs.py` 500 行（checkpoint 版本门卫 T141 逻辑独立为
  `services/case_resume_guard.py`）；
  `evals/adjudication_suite.py` 484 行（临时库装配/种子逻辑独立为
  `evals/adjudication_harness.py`，suite 保留编排与门禁）。
- **豁免四个**（AGENTS.md 约定补充说明）：schemas/api.py 379 与 services/db/models.py 354
  纯数据定义；app/api/v1/cases.py 367 与 services/memory/case_memory.py 315 略超线，
  拆分收益低于 churn 风险。约定改为"逻辑模块超 300 行考虑拆分，纯模型/数据定义豁免"。
- **验收**：拆后两文件 <300 行；全量 pytest + ruff + 主门 153 案 + 对抗门零退化
  （纯移动无逻辑改动，diff 应只含 import 与函数搬家）。

### 7.5 T154 Grafana 告警规则（无人值守的异常发现）

- **目标**：/metrics 有数据没人看 → 三个业务关键告警规则声明式进 Grafana
  provisioning（Grafana 11 unified alerting，不加独立 Alertmanager 容器——
  演示场景无真实通知接收端，规则命中即 UI/邮件通道按部署方接入）。
- **规则**：①自动结案率骤降（case 指标窗口对比）②交付队列积压（pending 深度
  阈值）③LLM 调用失败率阈值。
- **做法**：grafana/provisioning/alerting/ 规则 YAML + compose monitoring profile
  挂载；本地起栈验证规则加载（Grafana API 查询确认）。

### 7.6 批次验收

T150/T153/T154 本地全验证；T151/T152 CI 侧本地等价验证（真实 GitHub 运行
待 push）。每任务独立 commit（feat/ci/refactor 前缀）+ progress.md 追加。
