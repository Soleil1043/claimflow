# 任务清单 (Tasks)

> Phase 3 产出，Phase 4 规划于 2026-08-25 更新（D017/D018）。基于 plan.md 与架构文档拆解。
> 规则：1 个任务 = 1 个可独立验证的功能点 | 严格按顺序执行 | 不跳依赖
> 覆盖范围：MVP（F01-F14）+ Phase 3（工程化）+ Phase 4（深度亮点）

---

## 任务格式说明

```
- [ ] T0XX: [任务名] | 依赖: [T0XX或无] | 涉及文件: [路径] | 验收: [标准]
```

## 任务列表

### Phase 0：脚手架与基础设施（F01）

- [x] T001: 项目初始化与目录结构 | 依赖: 无 | 涉及文件: pyproject.toml、uv.lock、.env.example、项目根目录 | 验收: `uv sync` 成功安装依赖；目录结构与 AGENTS.md 第 5 节一致；git 仓库初始化并完成首次 commit
- [x] T002: 配置与日志基础 | 依赖: T001 | 涉及文件: app/core/config.py、app/core/logging.py、app/core/exceptions.py | 验收: 配置项从 .env 读取（含 APP_PROFILE / LLM_MODEL / LLM_VISION_MODEL）；structlog 输出结构化 JSON 日志；单元测试验证配置加载
- [x] T003: 数据库模型与会话管理 | 依赖: T002 | 涉及文件: services/db/models.py、services/db/session.py、alembic/、scripts/seed.py | 验收: 6 张表 ORM 模型定义完整；dev profile（aiosqlite）建表成功；prod profile 连接 PostgreSQL；alembic 迁移可执行（seed 脚本按依赖留到 T008 一并实现）
- [x] T004: FastAPI 骨架与健康检查 | 依赖: T003 | 涉及文件: app/main.py、app/api/v1/health.py、app/api/dependencies.py | 验收: `uv run uvicorn app.main:app` 启动成功；`GET /health` 返回 200 及 postgres/qdrant/redis/llm 各依赖连接状态
- [x] T005: Docker Compose 与 CI 流水线 | 依赖: T004 | 涉及文件: Dockerfile、docker-compose.yml、.github/workflows/ci.yml | 验收: `docker compose config` 校验通过（PostgreSQL/Qdrant/Redis/app 四服务）；本地 Docker 恢复可用，已完成完整容器化验证（compose up 全栈健康 + /health ok + alembic 容器内迁移成功）；CI push 验证待推 GitHub 后确认

### Phase 1：单 Agent ReAct MVP（F02-F07、F13、F14 部分）

- [x] T006: LLM 客户端封装 | 依赖: T004 | 涉及文件: services/llm/client.py、services/llm/prompts.py | 验收: 调用 `deepseek-v4-flash` 返回测试响应（真实调用验证合并到 T012 届时提供 API Key）；vision 模型独立配置项可调；供应商/模型均通过配置切换；LLM 调用有单测（mock 网络层）
- [x] T007: 工具层基础设施 | 依赖: T006 | 涉及文件: tools/base.py、tools/registry.py、tools/executor.py、schemas/tools.py | 验收: BaseTool 注册/发现/执行链路可用；ToolExecutor 超时、指数退避重试（≤2 次）、熔断（5 失败→30s）逻辑有单元测试
- [x] T008: Mock 数据与保单查询工具 | 依赖: T007 | 涉及文件: data/mock/*.json、tools/claim/policy_query.py、scripts/seed.py | 验收: `policy_query("POL-2025-0001")` 返回预置保单详情；不存在保单号返回 `success=false` 结构化错误；seed 脚本入库 3+ 张保单覆盖 active/expired/边界（F04）
- [x] T009: 理赔计算器工具 | 依赖: T008 | 涉及文件: tools/claim/calculator.py | 验收: 保额 100 万/免赔 1 万/比例 80% 用例计算正确；免赔额超保额等边界有单测（F05）
- [x] T010: RAG 知识库与检索工具 | 依赖: T008 | 涉及文件: data/kb_docs/*.md、services/rag/embedder.py、services/rag/retriever.py、services/rag/ingest.py、tools/claim/claim_rule_rag.py | 验收: 10-20 篇文档分块入库（Qdrant local mode + BGE-M3）；"阑尾炎手术有等待期吗"检索出相关条款 top-k 并含相似度排序（F06）
- [x] T011: 对话 API 与状态持久化 | 依赖: T004 | 涉及文件: app/api/v1/conversations.py、schemas/api.py、services/memory/short_term.py | 验收: A02-A05 四个接口可用（创建/列表/详情/历史，冒烟实测通过）；LangGraph Checkpoint 接入（CheckpointManager：dev=InMemorySaver，prod=AsyncPostgresSaver 含 setup 建表，T012 组图时接入主图）；同一会话多轮上下文连贯（依赖 A06 发消息，T012 一并验证 F02/F14）
- [x] T012: 单 Agent ReAct 核心流程 | 依赖: T011 | 涉及文件: state.py、nodes/generator.py、workflows/main_graph.py（Phase 1 简版图）、app/api/v1/conversations.py（A06） | 验收: 发消息"保单 POL-2025-0001 住院花了15800元能赔多少"真实 LLM 自动调用 policy_query → claim_calculator，回答含正确金额 4,640 元与计算明细，used_tools 轨迹完整；多轮上下文连贯（第二轮追问免赔额正确引用第一轮结果）；审计落库（F07 核心里程碑 + F02/F14 达成）
- [x] T013: 意图识别节点 | 依赖: T012 | 涉及文件: nodes/intent.py、data/mock/intent_test_cases.json | 验收: 20 条预置语句分类准确率 19/20 = 95%（真实 LLM，≥90% 验收线通过，0 次兜底）；未知输入走关键词规则兜底不报错（F03）
- [x] T014: Gradio 演示界面 | 依赖: T012 | 涉及文件: ui/app.py | 验收: 界面 HTTP 200 启动；verify_ui 脚本实测界面回调 → 后端 API → 工具链（4,640 元）→ 多轮上下文全链路通过（F13 基础版）

### Phase 2：多智能体协作（F08-F12、F13 完整、F14 完整）

- [x] T015: Agent 定义与 Prompt 体系 | 依赖: T012 | 涉及文件: agents/orchestrator.py、agents/claim.py、agents/medical.py、agents/compliance.py、services/llm/prompts.py | 验收: 4 个 Agent 各自 system prompt + 工具集 + 输出 schema 定义完成；结构化输出格式有 schema 校验单测（14 用例全绿）
- [x] T016: 医疗审核 Agent 工具 | 依赖: T008 | 涉及文件: data/mock/medical_records.json、tools/medical/record_query.py、tools/medical/diagnosis_matcher.py | 验收: "急性阑尾炎"→K35 covered=True 含保障范围结论；就诊记录查询返回预置数据（倒序）；材料缺失清单由 Medical Agent 输出 schema 承载（missing_materials 字段，T017 接入后生效）（F09）
- [x] T017: 任务规划与步骤执行节点 | 依赖: T015 | 涉及文件: nodes/planner.py、nodes/step_executor.py、schemas/agent.py | 验收: "我做了阑尾炎手术能赔多少"生成 ≥2 步计划（医疗审核→理赔核算）依次执行；每步结果写入 shared_data；执行记录可在响应追溯（F08）
- [x] T018: 合规审查节点与三态流转 | 依赖: T017 | 涉及文件: nodes/compliance.py、tools/compliance/rule_check.py、tools/compliance/risk_scoring.py、workflows/main_graph.py | 验收: 含"保证赔付"话术的回答被 MODIFY 拦截并给修改建议；高风险内容 REJECT 后不返回用户、标记转人工；条件边保证所有输出路径必经合规节点（F10）
- [x] T019: 敏感信息脱敏工具 | 依赖: T018 | 涉及文件: tools/compliance/sensitive_filter.py | 验收: 18 位身份证号输出 `3301**********1234` 格式；银行卡号/手机号脱敏；正则模式有单元测试覆盖（F11）
- [x] T020: OCR 图片上传（真实 OCR + Mock 兜底） | 依赖: T018 | 涉及文件: app/api/v1/conversations.py（A07）、tools/medical/ocr_extract.py、data/mock/ocr_fallback.json、ui/app.py（上传组件） | 验收: 上传诊断证明图片返回结构化字段（姓名/诊断/金额/日期）+ `source: vision`；模拟 vision API 异常时返回 `source: mock_fallback` 且接口不报错；非图片文件 422（F12）
- [x] T021: 主图组装与端到端联调 | 依赖: T018 | 涉及文件: workflows/main_graph.py（完整图：intent→分流→planner/step_executor/rag→compliance→generator/human_flag）、nodes/rag.py | 验收: A06 返回完整结构（answer/intent/used_tools/agent_steps/compliance_status/need_human_intervention）；多步任务全链路跑通；服务重启后历史会话可继续（F02 完整/F08/F14 完整）
- [x] T022: 端到端测试与场景完善 | 依赖: T021 | 涉及文件: tests/（workflows/agents/api 全量）、data/mock/ | 验收: 核心链路单测 + API 集成测试（httpx AsyncClient）全绿；覆盖正常/异常/边界场景（保单不存在、LLM 超时、合规拦截、Mock 兜底）

### 交付

- [x] T023: README 与最终验证 | 依赖: T022 | 涉及文件: README.md、.github/workflows/ci.yml | 验收: 对照 spec F01-F14 逐条核验通过；README 含安装/运行/API 文档/架构说明/CI 徽章；push 后 CI 全绿（push 待用户创建 claimflow 仓库后执行，本地 lint+test+compose 校验已全绿）

### Phase 3：工程化与优化（容错已随 MVP 达成，聚焦可观测性/评测/性能）

- [x] T024: Prometheus 指标埋点 | 依赖: T023 | 涉及文件: services/observability/metrics.py、app/main.py、tools/executor.py、services/llm/client.py、nodes/ | 验收: `GET /metrics` 暴露工具指标（调用成功率/耗时直方图/熔断计数）、LLM 指标（耗时/Token 消耗）、业务指标（转人工率/合规拦截率/平均处理时长）；埋点逻辑有单元测试（Counter/Histogram 注册与打点、标签维度正确）
- [x] T025: Prometheus + Grafana 容器化与仪表盘 | 依赖: T024 | 涉及文件: docker-compose.yml、prometheus/prometheus.yml、grafana/（dashboard JSON + datasource 自动配置） | 验收: `docker compose up` 后 Grafana（localhost:3000）自动加载仪表盘，含工具成功率、P95 延迟、LLM Token 消耗、转人工率、合规拦截率面板；本地容器化验证通过
- [x] T026: 评测数据集构建（200 条）| 依赖: T023 | 涉及文件: evals/datasets/*.json、scripts/gen_eval_dataset.py（辅助生成） | 验收: 200 条标注用例按架构 9.2 比例（FAQ 30 / 单领域 60：保单·医疗·合规各 20 / 多步复杂 80 / 边界异常 30），每条含「用户输入 + 期望工具调用序列 + 期望回答要点」；数据集 schema 有校验
- [x] T027: 评测运行器与指标计算 | 依赖: T026 | 涉及文件: evals/test_suite.py、evals/metrics.py | 验收: 真实 LLM 跑测试集输出报告（任务完成率/工具调用准确率/合规通过率/平均耗时/Token 消耗），支持子集运行（--category/--limit）与结果 JSON 落盘；基线报告生成
- [x] T028: Redis 工具结果缓存 | 依赖: T024 | 涉及文件: tools/base.py、tools/executor.py、services/cache.py、app/core/config.py | 验收: 幂等工具（policy_query/record_query/diagnosis_matcher/claim_rule_rag）相同入参二次调用命中缓存；TTL 可配（默认 300s）；缓存命中/未命中指标暴露；dev profile 内存降级；单测覆盖命中/过期/禁用三态
- [x] T029: Token 消耗统计与预算控制 | 依赖: T024 | 涉及文件: services/llm/client.py、app/api/v1/conversations.py | 验收: 每轮对话各环节（意图/规划/执行/生成）token 用量累计入 Prometheus 指标与结构化日志；单轮 token 超预算阈值时输出告警日志（不阻断）
- [x] T030: Phase 3 收尾验证 | 依赖: T027、T028、T029 | 涉及文件: README.md、.github/workflows/ci.yml | 验收: `uv run ruff check` + `uv run pytest` 全绿；评测基线报告产出并存档；README 补监控（/metrics、Grafana 访问）与评测（运行方式、基线指标）章节；push 后 CI 全绿

### Phase 4：深度亮点（GraphRAG / 长期记忆 / HITL 工作台 / OTel / A-B，D017/D018）

- [x] T031: 知识图谱构建 | 依赖: T030 | 涉及文件: services/rag/knowledge_graph.py、scripts/build_kg.py、data/graph/ | 验收: LLM 从 12 篇 kb_docs 抽取实体关系三元组（险种/疾病/等待期/免赔/免责等），内存图结构（邻接表 + 实体索引）+ JSON 落盘可复用（`scripts/build_kg.py` 幂等重建）；实体/关系 schema（Pydantic）校验 + 抽取失败重试/跳过容错；图谱统计（实体数/关系数/度分布）可输出
- [x] T032: 图检索与混合召回 | 依赖: T031 | 涉及文件: services/rag/graph_retriever.py、services/rag/retriever.py、nodes/rag.py | 验收: 图邻接扩展检索（疾病→险种→规则条款多跳）与 Qdrant 向量检索融合（RRF 或加权重排）；`claim_rule_rag` 工具输出增加 graph_context 维度；复杂关联问题（如"哪些疾病不在保障范围"）对比纯 RAG 召回可见提升；混合开关可配（GRAPH_RAG_ENABLED）
- [x] T033: GraphRAG 评测对比 | 依赖: T032 | 涉及文件: evals/datasets/（关联类用例扩充）、evals/test_suite.py | 验收: 评测集扩充 ≥20 条复杂关联用例（kb_docs 可溯源）；同一测试集跑"纯 RAG vs 混合召回"两组报告（变体切换复用 A/B 框架或 --variant 参数），量化任务完成率/检索命中差异；对比报告存档 evals/reports/
- [x] T034: 长期记忆写路径 | 依赖: T030 | 涉及文件: services/memory/long_term.py、app/api/v1/conversations.py（A06 出口） | 验收: 会话结束（或每 N 轮）生成对话摘要 + 关键实体（保单号/诊断/金额），BGE-M3 向量化入 Qdrant 独立 collection（按 user_id payload 过滤隔离）；摘要质量抽验；写路径幂等（重复会话不重复入库）
- [x] T035: 长期记忆读注入 | 依赖: T034 | 涉及文件: nodes/intent.py 或 generator.py、services/memory/long_term.py | 验收: 新会话首轮按 user_id 检索 top-k 历史摘要，注入 system prompt（Token 预算内）；跨会话上下文实测连贯（"我上次问的那张保单"正确引用历史）；无历史用户零影响（检索空直跳）
- [x] T036: HITL 工单后端 | 依赖: T030 | 涉及文件: services/db/models.py（HumanTicket）、app/api/v1/interventions.py、alembic/ | 验收: 转人工事件落工单（状态机 pending→resolved/transferred_out）；聚合上下文 API（会话轨迹 + tool_trace + agent_steps + compliance_result + intervention_reason）；坐席处理动作 API（解决/升级/回写结论）；单测覆盖状态流转
- [x] T037: LangGraph interrupt 恢复机制 | 依赖: T036 | 涉及文件: workflows/main_graph.py、nodes/compliance.py（REJECT 分支） | 验收: REJECT 路径接 LangGraph `interrupt`（替代直接 END）；坐席通过 A06 类接口以 `Command(resume=...)` 恢复会话，坐席结论经合规审查后返回用户；interrupt 状态持久化（checkpoint）可跨服务重启恢复；端到端单测（触发→interrupt→人工结论→恢复→返回）
- [ ] T038: Next.js 人工介入工作台 | 依赖: T036、T037 | 涉及文件: workbench/（Next.js 15 + React 19 + Tailwind 新目录）、README | 验收: 转人工会话列表页（状态筛选）+ 详情页（对话轨迹/工具调用/Agent 步骤/合规拦截原因可视化渲染）+ 处理动作（解决并回写结论，触发 interrupt 恢复）；dev 代理直连后端 8000；README 补工作台章节（启动方式 + 截图占位）
- [ ] T039: OTel + Jaeger 全链路追踪 | 依赖: T030 | 涉及文件: services/observability/tracing.py、docker-compose.yml（tracing profile）、pyproject.toml | 验收: OpenTelemetry SDK 埋点（FastAPI instrumentation + LLM/工具调用 span，trace_id 贯穿 A06→节点→工具）；compose `--profile tracing` 起 Jaeger + OTel Collector；本地起栈在 Jaeger UI 看到完整调用树（span 含 token 用量/工具名/合规裁决属性）；采样率可配
- [ ] T040: A/B 实验框架 | 依赖: T030 | 涉及文件: evals/ab_test.py、evals/variants.py（或配置文件） | 验收: 实验配置定义变体（模型/参数/prompt 路径切换）；同一评测集分流运行多变体并产出对比报告（复用 metrics 聚合，组间差异 + 显著性粗判）；结果 JSON 落盘；--variant 参数与 test_suite 兼容
- [ ] T041: A/B 实战实验（deepseek-v4-pro 对比） | 依赖: T040 | 涉及文件: evals/reports/、.agent/decisions.md（实验结论 D019） | 验收: deepseek-v4-flash（基线）vs deepseek-v4-pro 200 条全量对比，产出结论报告（任务完成率/工具准确率/耗时/token 成本四维对比 + 选型建议写入 decisions.md）；预算 ≤ ¥10
- [ ] T042: Phase 4 收尾验证 | 依赖: 全部 | 涉及文件: README.md、docs/architecture.md、.github/workflows/ci.yml | 验收: ruff + pytest 全绿；GraphRAG/长期记忆/工作台/OTel/AB 五个方向的 README 章节与架构图更新；D017/D018/D019（实验结论）齐备；push 后 CI 全绿

---

## 依赖关系图

```
T001 → T002 → T003 → T004 → T005
                     ↓
              T006 → T007 → T008 → T009
                     │       ↓
                     │       T010（RAG）
                     ↓       ↓
              T011 → T012 → T013
                ↓      ↓ ↓
                ↓      ↓ T014（界面）
                ↓      ↓ T015 → T017 → T018 → T019
                ↓      ↓                ↓
                ↓      ↓                T020（OCR）
                ↓      ↓                ↓
                ↓      └──── T016 ──→ T021 → T022 → T023
                                                ↓
        Phase 3:  T024（指标埋点）→ T025（Prometheus+Grafana）
                  T024 → T028（工具缓存）
                  T024 → T029（Token 统计）
                  T023 → T026（评测集）→ T027（评测运行器）
                  T027 + T028 + T029 → T030（收尾）
                                  ↓
        Phase 4:  T030 → T031（图谱构建）→ T032（混合召回）→ T033（GraphRAG 评测对比）
                  T030 → T034（记忆写）→ T035（记忆读注入）
                  T030 → T036（工单后端）→ T037（interrupt 恢复）→ T038（Next.js 工作台）
                  T030 → T039（OTel+Jaeger）
                  T030 → T040（A/B 框架）→ T041（v4-pro 实战实验）
                  全部 → T042（收尾）
```

## 进度统计

- 总任务数：42（MVP 23 + Phase 3 七个 + Phase 4 十二个）
- 已完成：34
- 进行中：0
- 待开始：8（T038-T042，Phase 4）
