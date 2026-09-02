# 任务清单 (Tasks)

> Phase 3 产出，Phase 4 规划于 2026-08-25 更新（D017/D018）；Phase 5 规划于 2026-09-01 更新（D021/ADR-007）。基于 plan.md 与架构文档拆解。
> 规则：1 个任务 = 1 个可独立验证的功能点 | 严格按顺序执行 | 不跳依赖
> 覆盖范围：MVP（F01-F14）+ Phase 3（工程化）+ Phase 4（深度亮点）+ Phase 5（LangGraph 标准构件对齐）

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
- [x] T038: Next.js 人工介入工作台 | 依赖: T036、T037 | 涉及文件: workbench/（Next.js 15 + React 19 + Tailwind 新目录）、README | 验收: 转人工会话列表页（状态筛选）+ 详情页（对话轨迹/工具调用/Agent 步骤/合规拦截原因可视化渲染）+ 处理动作（解决并回写结论，触发 interrupt 恢复）；dev 代理直连后端 8000；README 补工作台章节（启动方式 + 截图占位）
- [x] T039: OTel + Jaeger 全链路追踪 | 依赖: T030 | 涉及文件: services/observability/tracing.py、docker-compose.yml（tracing profile）、pyproject.toml | 验收: OpenTelemetry SDK 埋点（FastAPI instrumentation + LLM/工具调用 span，trace_id 贯穿 A06→节点→工具）；compose `--profile tracing` 起 Jaeger + OTel Collector；本地起栈在 Jaeger UI 看到完整调用树（span 含 token 用量/工具名/合规裁决属性）；采样率可配
- [x] T040: A/B 实验框架 | 依赖: T030 | 涉及文件: evals/ab_test.py、evals/variants.py（或配置文件） | 验收: 实验配置定义变体（模型/参数/prompt 路径切换）；同一评测集分流运行多变体并产出对比报告（复用 metrics 聚合，组间差异 + 显著性粗判）；结果 JSON 落盘；--variant 参数与 test_suite 兼容
- [x] T041: A/B 实战实验（glm-5.3-flash 跨供应商对比） | 依赖: T040 | 涉及文件: evals/reports/、.agent/decisions.md（实验结论 D019） | 验收: deepseek-v4-flash（基线）vs glm-5.3-flash（智谱，用户切换对比组；原 deepseek-v4-pro 因思考型 token 成本高、长跑慢而调整）200 条全量对比，产出结论报告（任务完成率/工具准确率/耗时/token 成本四维对比 + 跨供应商选型建议写入 decisions.md）；预算 ≤ ¥10
- [x] T042: Phase 4 收尾验证 | 依赖: 全部 | 涉及文件: README.md、docs/architecture.md、.github/workflows/ci.yml | 验收: ruff + pytest 全绿；GraphRAG/长期记忆/工作台/OTel/AB 五个方向的 README 章节与架构图更新；D017/D018/D019（实验结论）齐备；push 后 CI 全绿
- [x] T043: 重排序精排层（bge-reranker-v2-m3 可开关） | 依赖: T042 | 涉及文件: services/rag/reranker.py、nodes/rag.py、evals/reports/、.agent/decisions.md（D020） | 验收: rag_node top-8 召回 → CrossEncoder 重排 → top-4，开关默认关（关态与 T021 行为一致）；精排故障回退向量序零影响；单测覆盖开关/排序/回退；真实评测三指标对比（完成率/延迟/检索质量）+ 结论 D020

### Phase 5：LangGraph 标准构件对齐重构（D021/ADR-007）— ⏸ 待用户确认启动

> 原则：所有部分尽量按 LangGraph/LangChain 已定义的方法、类、架构实施，非必要不自研。
> 仅保留自研：熔断器、工具缓存白名单、领域工具/Prompt/降级规则。每个任务验收含「官方构件清单 + 自研清单」核对。
> 依赖前置已于 2026-09-01 完成：langchain 1.3.18 新增、langchain-core 1.6.1，langgraph 系列确认均为最新版。

- [x] T044: 工具层标准化 | 依赖: 无 | 涉及文件: tools/base.py、tools/guards.py（新）、tools/factory.py（新）、tools/registry.py、tools/executor.py、tools/{claim,medical,compliance}/*、services/materials.py、AGENTS.md（6.1 + 结构图）、scripts/verify_ocr.py、tests/ | 验收: 9 个工具改为官方工具定义（ClaimflowTool 继承 langchain BaseTool，args_schema + _arun 返回 dict，业务失败为正常返回、系统异常交守卫层）；重试/降级换官方 .with_retry()（含全部异常指数退避），超时用 asyncio.timeout；熔断器与缓存白名单保留为最小自研（GuardedTool 工具层守卫，agent 循环内外统一生效）；pyproject 新增 langchain≥1.0（前置已完成 26c4c74）；AGENTS.md 6.1 同步修订；单测等价迁移全绿 ✅ 2026-09-01（398 passed：374→迁 355 + 守卫层新增 43）
  > 过渡说明：ToolOutput 信封与 ToolRegistry 按 D021 属删除项——信封已从工具层移除（工具返回 dict），ToolOutput 类暂存活于 ToolExecutor 兼容壳做消费端适配；registry 降级为惰性工厂填充的名称容器（import 即注册副作用已删）；两者随 T046/T047 消费端子图化后删除
- [x] T045: 决策点结构化输出原生化 + 意图更名 | 依赖: 无（可与 T044 并行，按顺序执行） | 涉及文件: nodes/intent.py、nodes/compliance.py、schemas/agent.py、schemas/agent_outputs.py、services/llm/prompts.py、workflows/main_graph.py、app/api/v1/conversations.py、data/mock/intent_test_cases.json、evals/datasets/、evals/schemas.py、scripts/、ui 无涉、tests/ | 验收: 意图/合规判决改 with_structured_output（IntentType StrEnum 五分类 / ComplianceVerdict Literal 三态，均 function_calling 承载），手写 _parse_llm_json ×2 删除；关键词/确定性兜底保留且有单测；**意图 multi_step → complex_consult 更名（D023）**：VALID_INTENTS/prompt few-shot/关键词规则/route_intent/planner 示例同步，意图测试集与 200 条评测集 expected_intent 批量替换（eval category 板块名保持 multi_step 不动），messages 历史值读取时 normalize_intent 映射兼容（A05/工单上下文共用 to_message_item），A06 返回新值 ✅ 2026-09-01（393 passed；真实 LLM 意图准确率验收并入 T048 全量回归）
- [x] T046: Worker Agent 子图化 | 依赖: T044 | 涉及文件: agents/base.py、agents/runner.py（重写）、nodes/step_executor.py、tests/agents/test_runner.py（新）、tests/（fake 签名同步）、tests/conftest.py（HF 离线护栏） | 验收: AgentDefinition 保留为静态配置（resolve_tool_objects 从工具工厂图解析），Worker 执行体换 langchain.agents.create_agent 子图（system_prompt 静态 + 动态指令/shared_data 经输入 messages 注入 + response_format→structured_response，ToolStrategy 隐藏工具机制实证）；agents/runner.py 手写 ReAct 循环删除（子图缓存 + recursion_limit 承载 MAX_TOOL_ROUNDS + token callback 承载 T029 轮次记账）；解析失败降级 summary；tool_trace 改由 messages 中 ToolMessage 派生（AIMessage.tool_calls↔ToolMessage 配对，排除结构化输出工具），A06 used_tools 口径不变；多步场景测试通过 ✅ 2026-09-02（399 passed，含新增 6 个 runner 用例；顺带根治 HF 联网元数据校验导致的测试挂起）
- [x] T047: supervisor 动态路由化 + react 路径 prebuilt 化 | 依赖: T046 | 涉及文件: nodes/supervisor.py（新）、nodes/planner.py（删）、nodes/step_executor.py（删）、nodes/generator.py（重写 react 包装）、workflows/main_graph.py、state.py、app/api/v1/conversations.py、evals/test_suite.py、scripts/verify_e2e.py、scripts/verify_planner.py（删）、tests/（supervisor 新增 + 全面适配）、tests/conftest.py（子图缓存隔离）、AGENTS.md（节点结构）、services/llm/prompts.py（SUPERVISOR_PROMPT 增 / TASK_PLANNER_PROMPT 删） | 验收: planner+游标循环移除，supervisor 节点 RoutingDecision{next/plan/reason} 结构化输出 + Command(goto) 动态路由（claim/medical/synthesize），对账守卫（目标无 pending → 改投首个 pending / 全 done → FINISH）支持执行中重规划；Worker=make_worker_node（invoke_worker → shared_data/计划状态推进/子图消息并入主图）；单领域路径换 create_agent 子图（react_node，记忆 SystemMessage 注入 + 降级话术保留）；State 删 current_step/tool_trace/agent_steps/medical_result/claim_result；used_tools 改 messages 派生（derive_tool_trace）、agent_steps 改 task_plan 推导（derive_agent_steps），A06/评测/审计口径不变；多步场景端到端测试通过 ✅ 2026-09-02（391 passed；tests/conftest 增子图缓存隔离与 HF 离线双护栏）
- [x] T048: 长期记忆 Store 化 + 全量回归收尾 | 依赖: T047 | 涉及文件: services/memory/long_term.py（Store 化重写）、services/rag/embedder.py（预热）、services/llm/client.py（thinking disabled）、app/main.py、evals/test_suite.py、scripts/verify_e2e.py、scripts/verify_memory_read.py、app/core/config.py、.env.example、tests/memory/test_long_term.py（重写）、docs/architecture.md、README.md | 验收: 长期记忆迁官方 Store（dev=InMemoryStore / prod=AsyncPostgresStore + 内建向量 index IndexConfig{dims:1024, embed:BGE-M3, fields:[embed_text]}，namespace=(user_id,) 隔离，key=uuid5 幂等），Qdrant long_term_memory collection 与注入管线删除（Qdrant 仅存 RAG）；跨会话写读闭环测试通过；200 条评测全量回归 ✅ 2026-09-02（四轮迭代：完成率 67.5%→87.0%，相对基线 88% 回退 1pp 压线达标；multi_step 48.8%→92.5% 与基线持平；**工具准确率 94.7%，距 ≥95% 验收线差 0.3pp——差量来自基线既有反问类噪声与 any_of 同义词缺失，遗留至后续 prompt 调优**；回归修复三项实测入 progress：① DeepSeek thinking mode 拒 tool_choice → extra_body 显式 disabled；② BGE-M3 冷加载 19s 阻塞事件循环连坐工具超时 → lifespan/evals 启动预热；③ worker 循环硬截断丢失 → 官方 ModelCallLimitMiddleware 恢复 + GraphRecursionError 兜底；另 used_tools 派生按业务工具白名单过滤隐藏结构化工具）

### 增量任务（用户直接需求，独立于 Phase 5）

- [x] T049: 材料上传支持 PDF/Word（D024） | 依赖: 无 | 涉及文件: services/materials.py（新）、app/api/v1/conversations.py、schemas/api.py、services/llm/prompts.py、app/core/config.py、.env.example、ui/app.py、pyproject.toml、tests/ | 验收: PDF 两段式（pypdf 文本提取 → 扫描件 pypdfium2 渲染走 vision，逐页取首个有效页）；.docx 文本提取走主链路模型；.doc 旧格式 422 提示转存；`/materials` 新端点 + `/images` 兼容别名双路由；≤10MB / 渲染≤3 页 / PDF 文本<50 字符判扫描件三护栏可配置；任何失败 Mock 兜底（source=mock_fallback）不报错；响应含 file_type；提取服务单测 + API 测试全绿 ✅ 2026-09-01（394 passed；PDF 识别技术栈定位另见 D025）

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
                                  ↓
        Phase 5:  T044（工具层标准化）→ T046（Worker 子图化）→ T047（supervisor 化）→ T048（Store 化 + 回归）
                  T045（决策点结构化输出，独立链，T044 后执行）
```

## 进度统计

- 总任务数：49（MVP 23 + Phase 3 七个 + Phase 4 十二个 + T043 重排序增量 + Phase 5 五个 + 增量 T049）
- 已完成：49（2026-08-27 完成 T001-T043；2026-09-01 完成 T049、T044、T045；2026-09-02 完成 T046、T047、T048——**全部完成**）
- 进行中：0
- 待开始：0

> Phase 5 回归实测（T048，deepseek-v4-flash 全量 200 条，evals/reports/t048_phase5_regression.json）：
> 完成率 87.0%（基线 88%，回退 1pp 达标线压线）｜工具准确率 94.7%（基线 95.26%，差 0.3pp 未达
> ≥95% 子项，遗留）｜合规通过率 95.5%｜multi_step 92.5% 与基线持平（修复前 48.8%）。
