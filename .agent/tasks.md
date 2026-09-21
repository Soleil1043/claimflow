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
- [x] T050: 轨迹质量评测（D026） | 依赖: 无（T027/T040 评测框架上增量） | 涉及文件: evals/schemas.py、evals/trajectory.py（新）、evals/metrics.py、evals/test_suite.py、evals/datasets/eval_dataset.json（抽样标注）、tests/evals/test_trajectory.py（新）、.agent/decisions.md | 验收: EvalCase 增轨迹期望字段（expected_tool_order/forbidden_tools/expected_route/max_tool_calls/expected_tool_args，全可选向后兼容）；CaseResult 存按序轨迹摘要 {agent, tool, input}（output 不入库）+ task_plan 派生路由；判分纯函数（LCS 按序子序列匹配 / 禁调 / 次数上限与冗余计数 / 入参子集断言）；报告新增独立 trajectory 指标块，passed 五维判分不变（D026：轨迹暂不列入 passed）；数据集抽样标注 ≥12 条（multi_step 顺序与入参 / edge_case 禁调）；单测覆盖判分规则与聚合口径 ✅ 2026-09-02（408 passed；真实冒烟 FAQ×3 + MS-001：顺序/路由/入参端到端产出，两轮 MS 对比暴露调用次数波动 7+→6——轨迹指标捕捉到答案层不可见的行为方差）
- [ ] T051: 评测 UI 界面（D027） | 依赖: 无（T027 评测运行器 + T014 Gradio 形态上增量） | 涉及文件: services/eval_runner.py（新）、app/api/v1/evals.py（新）、schemas/api.py、app/main.py、ui/eval_app.py（新）、tests/api/test_evals.py（新）、README.md、.agent/decisions.md | 验收: UI 一键启动评测（数据集/分类/变体/条数上限参数）+ 实时查看进度（逐用例 PASS/FAIL 日志与进度计数）+ 查看评测结果（历史报告列表 + 汇总指标/分类明细/轨迹指标/失败用例表）；后端 /api/v1/evals 路由（runs 启动/列表/状态 + reports 列表/详情 + meta），评测子进程隔离执行（D027：不污染 API 服务单例），单活跃运行守卫（并发 409）；单测覆盖运行生命周期与报告接口（假命令注入，不跑真实 LLM） ✅ 2026-09-02（414 passed；端到端实测：8001 起后端 → API 发起 3 条真实评测 8 轮轮询到完成 → 报告落盘并回链；评测台 7861 正常伺服，全部回调（poll/report/meta/refresh）对真实后端驱动验证通过；并发 409/失败退出码/路径穿越守卫由假命令单测覆盖）
- [x] T052: 评测运行历史持久化 + git_sha（D028） | 依赖: 无（T051 运行管理器上增量） | 涉及文件: services/db/models.py（eval_runs 表）、alembic/versions/（迁移）、services/eval_history.py（新）、services/eval_runner.py、evals/test_suite.py、schemas/api.py、app/api/v1/evals.py、ui/eval_app.py、tests/api/test_evals.py、.agent/decisions.md | 验收: eval_runs 表落库（run_id/来源/参数/状态/exit code/git_sha/计数/率冗余列/summary/日志尾/起止时间）；UI 运行由 EvalRunManager 两阶段写（start=running / finish=终态），CLI 运行由 test_suite 收尾自记（EVAL_MANAGED_BY=api 防双写）；历史写入 fail-open（DB 故障不影响评测）；/runs 合并内存+DB（重启后历史可查）；报告 JSON 增 git_sha 字段；历史写入/查询/合并有单测 ✅ 2026-09-02（420 passed；迁移 upgrade/downgrade/再 upgrade 在临时 SQLite 全程验证；真实 e2e：API 发起 2 条评测 → DB 行 source=ui/completed/git_sha=f8b7d48/率值齐备；发现并修复内存终态覆盖 DB 行致率值丢失的合并缺陷；本地 dev 库 alembic stamp 同步 head）
- [x] T053: 评测趋势对比图（D029） | 依赖: T052 | 涉及文件: schemas/api.py、app/api/v1/evals.py（/trends）、ui/eval_app.py（gr.Plot）、pyproject.toml（plotly 依赖）、tests/api/test_evals.py、.agent/decisions.md | 验收: GET /api/v1/evals/trends 合并 DB 历史与 reports 文件两个来源（按 report_name 去重，时间升序），每点含完成率/工具准确率/变体/git_sha/来源标签；UI 趋势区按数据集+变体过滤，双指标折线（hover 显示 run 标签与 commit）；端点合并逻辑与图形构建有单测 ✅ 2026-09-02（420 passed；/trends 实测 14 点：13 个历史存量报告 + 1 个 DB 行，时间升序、去重正确；UI 趋势区对真实后端出 plotly 双 trace 折线，hover 含 run 标签/变体/commit，数据集+变体过滤生效；运行结束后曲线随轮询自动刷新）

### Phase 6：三界面设计优化（D030，Apple Design 移植）— 2026-09-02 启动

> 硬约束：纯表现层——不改 API 契约 / 业务逻辑 / Gradio 回调元数；设计语言见 plan.md 第 7 节。

- [x] T054: 设计令牌与共享主题 | 依赖: 无 | 涉及文件: ui/theme.py（新）、tests/ui/test_theme.py（新）、workbench/app/globals.css | 验收: ui/theme.py 导出 build_theme()/APP_CSS（Apple 调色板/系统字体栈/材质/按压/reduced-motion 齐备）；workbench globals.css 定义同名同值设计变量 + .card/.btn/.pill 工具类；主题模块单测通过 ✅ 2026-09-02（4 passed；Gradio 6 主题 API 适配：button_shadow 移除，改 button_primary_shadow/button_transform_active）
- [x] T055: 用户聊天界面重构 | 依赖: T054 | 涉及文件: ui/app.py | 验收: 应用共享主题与 CSS（半透明吸顶头部/气泡重排/示例 chips/工具轨迹折叠卡片/输入区材质）；启动 HTTP 200；浏览器截图目检层级与材质正确；既有聊天回调逻辑零改动 ✅ 2026-09-02（截图 docs/diagrams/t055_chat_ui.png：浮层头部 + 后端健康状态点（demo.load 探测 /health）+ 气泡 + 材质输入区 + chips；Gradio 6 theme/css 移至 launch()；chips 选择器提权覆盖 .gallery-item 默认透明样式；回调零改动，8 passed）
- [x] T056: 评测台界面重构 | 依赖: T054 | 涉及文件: ui/eval_app.py | 验收: 应用共享主题；报告摘要 KPI 大数字卡；HTML 渐变进度条 + 状态 pill 替代纯文本；日志暗色等宽块；poll 8 输出元数不变、tests/ui/test_eval_app.py 4 用例全绿；截图目检 ✅ 2026-09-02（8 passed；整页截图 + 真实报告静态渲染预览 docs/diagrams/t056_render_preview.png：KPI 大数字卡/渐变进度条/轨迹 pill 均正确）
- [x] T057: 坐席工作台重构 | 依赖: T054 | 涉及文件: workbench/app/{layout,page,globals.css}、workbench/app/tickets/[id]/page.tsx、workbench/components/*.tsx | 验收: 全站 sticky 毛玻璃导航；列表/详情/时间线/审计/表单/徽章全部消费设计令牌；:active 按压反馈与 focus ring；reduced-motion/reduced-transparency 媒体查询生效；`npm run build` 通过；截图目检 ✅ 2026-09-02（build 通过；列表/详情截图目检：分段控件/KPI 风险分/气泡/四态 pill 均正确）
- [x] T058: Phase 6 收尾验证 | 依赖: T055、T056、T057 | 涉及文件: README.md、.agent/decisions.md（D030）、.agent/progress.md | 验收: ruff + pytest 全量绿；README 三界面章节更新（设计系统说明 + 启动方式不变）；D030 与 progress 回填；逐任务 commit 齐备 ✅ 2026-09-02（428 passed 全绿；README 新增「7. 界面设计系统」章节；T054-T057 逐任务 commit）

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
                                  ↓
        Phase 6:  T054（设计令牌/主题）→ T055（聊天 UI）→ T056（评测台 UI）→ T057（坐席工作台）→ T058（收尾）
```

## 进度统计

- 总任务数：93（v1 咨询版 76 项 T001-T076 全部完成；Phase 8 核赔重写 17 项 T077-T093）
- 已完成：76 + T077（2026-09-04 立项）
- 进行中：0
- 待开始：16（T078-T093）

> Phase 5 回归实测（T048，deepseek-v4-flash 全量 200 条，evals/reports/t048_phase5_regression.json）：
> 完成率 87.0%（基线 88%，回退 1pp 达标线压线）｜工具准确率 94.7%（基线 95.26%，差 0.3pp 未达
> ≥95% 子项，遗留）｜合规通过率 95.5%｜multi_step 92.5% 与基线持平（修复前 48.8%）。

---

### Phase 6.5：Gradio 双界面视觉深化（D031，impeccable 方法论）

- [x] T059: 设计系统视觉深化（共享层） | 依赖: T058 | 涉及文件: ui/theme.py、tests/ui/test_theme.py | 验收: 分层阴影/入场动效/区块标题体系/选区/滚动条/footer 隐藏/焦点光环/按钮 hover 升起落 CSS；pytest 主题用例（含新增 polish 断言）全绿
- [x] T060: 演示界面视觉深化 | 依赖: T059 | 涉及文件: ui/app.py | 验收: 头部品牌 logo 块 + 状态 pill 化、气泡/composer/chips 精修、交错入场；回调元数与业务逻辑不变
- [x] T061: 评测台视觉深化 | 依赖: T059 | 涉及文件: ui/eval_app.py | 验收: 趋势/历史报告 Tabs 分区、状态卡化、KPI hover 升起、分类明细 mini 进度条、区块标题体系；poll 8 输出元数不变
- [x] T062: 视觉深化验证与文档 | 依赖: T060、T061 | 涉及文件: README.md、docs/diagrams/、.agent/progress.md | 验收: ruff + pytest 全绿；两界面 headless 截图目检通过；README 界面章节更新

---

### 增量：Next.js 对话界面（D032，2026-09-03 追加）

- [x] T063: 对话界面 Next.js 重写（chatui/） | 依赖: T058 | 涉及文件: chatui/**、README.md、AGENTS.md、.agent/decisions.md（D032） | 验收: `npm run build` 通过；功能对齐 Gradio 演示界面（会话惰性创建 / 发消息 / 材料上传识别 / 后端健康状态 / 示例问题 / 新会话）；A06 富数据结构化展示（意图与合规三态 pill、agent_steps 处理过程、工具轨迹折叠明细、转人工提示卡）；真实后端对话冒烟 + headless 截图目检；README 界面章节更新

---

### Phase 7：评测体系强化（D033，依据 docs/eval-audit-for-ai.md 审计）— 2026-09-03 启动

> 目标三段：P0 北极星闭环（转人工指标可测可证）→ P1 判分深度（数值断言/judge/轨迹全量/多轮）
> → P2 防线闭环（对抗集/CI 门禁/统计增强）→ T074 全量回归换基线。
> 缺陷对照：BUG-001/002（P0）、GAP-001/003/004（P1）、GAP-005/006/007/008（P2）、次要问题（并入 T069/T073）。

- [x] T064: 修复转人工指标（BUG-001） | 依赖: 无 | 涉及文件: evals/metrics.py、tests/evals/test_scoring.py | 验收: precision=实际转人工中「确实该转」占比、recall=期望转人工中被转的占比（CaseResult 增 expect_human 透传）；空集 0.0，不再恒 0/1 退化；单测全对/全错/空集/混合 4 用例 ✅ 2026-09-03（17 passed）
- [x] T065: 转人工期望用例 18 条（human_handoff 类目，BUG-001 分母） | 依赖: T064 | 涉及文件: evals/schemas.py、evals/datasets/eval_dataset.json、scripts/gen_hitl_cases.py、tests/evals/test_dataset.py | 验收: HUMAN_HANDOFF 入 EvalCategory 并进主数据集（200→218，版本升 1.1.0）；覆盖合规 REJECT（骗保/高风险表述）/材料严重缺失/保障外情绪激动/法律纠纷/用户主动要求五类；每条 expect_human_intervention=true + must_not_include 违规承诺话术；`--category human_handoff` 真实跑通，precision/recall 非退化且可解释 ✅ 2026-09-03（真实运行 18 条：human_scored=18/intervened=0，recall 0/18——系统侧无这些转人工触发路径，考卷首次量化北极星缺口，属测量成功而非考卷缺陷；系统行为优化留后续任务）
- [x] T066: 意图准确率进报告（BUG-002） | 依赖: 无 | 涉及文件: evals/metrics.py、evals/test_suite.py、tests/evals/test_scoring.py | 验收: CaseResult.intent_match（None=未标注不考核）+ actual_intent 回填（run_case 从 state intent 取）；EvalReport 增 intent_accuracy/intent_scored；80 条标注计入分母；单测 ✅ 2026-09-03（68 passed；空分母 None 非 1.0，D033 口径；T065 冒烟进程早于本改动启动，intent 列于全量回归时产出）
- [x] T067: 数值精确断言（GAP-001a） | 依赖: 无 | 涉及文件: evals/schemas.py、evals/metrics.py、scripts/annotate_numbers.py（一次性迁移）、evals/datasets/*.json、tests/evals/ | 验收: EvalCase 增 expected_numbers；判分数字归一化（千分位/全角/纯零小数尾折叠）+ 边界断言（640 不混过 4640、4640 不粘连 4640.5）、并入 passed；**仅迁 must_include**（AND→AND 安全），any_of 纯数字不迁（OR 同义容错语义保留，如 "80%" vs "1.0"）；迁移 7 条（POL-005/006、MS-001/005/009、GA-011/020，归一化去重）；容差与边界单测 ✅ 2026-09-03（72 passed）
- [x] T068: LLM-as-judge 二层判分（GAP-001b） | 依赖: 无 | 涉及文件: evals/judge.py（新）、evals/test_suite.py（--judge 开关 + Qdrant 副本隔离）、evals/metrics.py、tests/evals/test_judge.py（新） | 验收: rubric 三维（事实一致性/完整性/合规性）0-2 分、总分 ≥4 判过；仅判 must_include 为空的用例；judge 结果独立列不并入 passed（同 D026 先观察）；LLM mock 单测；校准流程（人工抽检 50 条对齐率 ≥85% 方可采信）写入 decisions ✅ 2026-09-03（80 passed；真实冒烟 3/3：好答案 4 分过/违规答案 0 分挂；**顺带修两个环境坑**：①DeepSeek 拒 json_schema response_format→judge 改 method=function_calling ②常驻 API 进程持 Qdrant local 文件锁致评测向量检索静默 0 命中→build_eval_graph 增加 Qdrant 进程级副本隔离）
- [x] T069: 轨迹标注扩展 + graph_assoc 类目修正（GAP-003 + 次要问题） | 依赖: 无 | 涉及文件: evals/datasets/eval_dataset.json、evals/datasets/eval_graph_assoc.json、scripts/collect_traces.py（轨迹参考收集）、scripts/annotate_trajectory.py（半自动标注）、tests/evals/ | 验收: 真实跑 multi_step 80 条收集轨迹参考（68/80 passed，route 主流 [medical,claim]）→ 规则标注 47 + 采纳稳定形态 11 + 语义手工 4 → order 64/route 34（合计 98 ≥80；未标满有原则：纯 RAG 路径 task_plan 空 route 无从考核、claim_rule_rag 双 Agent 二义、病态/波动轨迹不固化）；graph_assoc 24 条 category 修正 v1.1.0；**顺带修 test_dataset 工具名笔误**（medical_record_query/claim_status_query→以工厂注册名为准）；数据集校验单测过 ✅ 2026-09-03（96 passed）
- [x] T070: 多轮对话用例集 30 条（GAP-004） | 依赖: 无 | 涉及文件: evals/schemas.py（turns 字段）、evals/test_suite.py（逐轮同 thread）、evals/datasets/eval_multiturn.json（新）、scripts/gen_multiturn_cases.py、tests/evals/test_multiturn.py（新） | 验收: turns 默认空=单轮（向后兼容）；run_case 逐轮 ainvoke 同 thread、末轮判分（首轮带完整 state，后续轮增量 messages 由 checkpoint 恢复）；场景覆盖上文指代×10/追问×8/中途改口×6/冲突纠正×6；单测 fake graph 验证多轮口径；3 条真实冒烟 ✅ 2026-09-03（87 passed；user_input=turns[0] 约定固化；真实冒烟 3/3 PASS——MT 指代/追问/改口全过，p95/CI/token 新指标同步产出）
- [x] T071: 安全对抗用例集 20 条（GAP-005） | 依赖: 无 | 涉及文件: evals/datasets/eval_adversarial.json（新）、evals/schemas.py（ADVERSARIAL 类目）、scripts/gen_adversarial_cases.py、tests/evals/ | 验收: prompt 注入/越权查询/PII 诱导/违规承诺诱导/错别字方言五类各 4 条；独立数据集不污染主基线（D033）；must_not_include 断言具体违规输出物（完整证件号/承诺原话，否定语境不误杀）；E 类鲁棒性题期望正常服务（4640 照算）；数据集校验过 ✅ 2026-09-03
- [x] T072: CI 评测回归门禁（GAP-007） | 依赖: T064 | 涉及文件: .github/workflows/ci.yml、scripts/check_eval_gate.py（新）、evals/metrics.py（wilson_ci）、tests/evals/test_eval_gate.py（新） | 验收: PR 触发 smoke `--limit 20` 门禁；LLM_API_KEY secret 缺失时 skip 不 fail（env 探测步）；门禁脚本：完成率降幅 >5pp 退出码 1（Wilson CI 口径提示）；HF 模型 cache（actions/cache）；CI 内 ingest 重建向量索引（data/qdrant 不入库）；脚本单测 4 用例 + 本地对 baseline.json 手工验证 ✅ 2026-09-03（92 passed；真实 CI 运行待 push 后确认）
- [x] T073: 报告统计增强（GAP-006/008 + 次要问题） | 依赖: 无 | 涉及文件: evals/metrics.py（wilson_ci/p95/tokens）、evals/test_suite.py（token 差分下沉）、schemas/api.py（task_completion_ci）、app/api/v1/evals.py、ui/eval_app.py、tests/evals/ | 验收: EvalReport 增 p95_duration_s（最近秩法）/tokens_per_case（aggregate(tokens_total=) 注入）/wilson_ci；趋势点带 CI 且 UI hover 展示；UI 轨迹维度与工具准确率 scored=0 显式 N/A（消除"无数据=满分"误读）；UI 增转人工召回/意图准确率 KPI 卡（有标注才显示）✅ 2026-09-03（160 passed 含 UI/API 回归）
- [x] T075: 评测台 Phase 7 能力对齐 | 依赖: T074 | 涉及文件: schemas/api.py、services/eval_runner.py、ui/eval_app.py、tests/api/test_evals.py、tests/ui/test_eval_app.py、docs/diagrams/t075_*.png | 验收: judge 开关打通 UI 全链路（EvalRunStartRequest.judge → EvalRunParams → 子进程 --judge）；报告渲染补 judge 判过率 KPI 卡；真实渲染双截图目检（界面 Checkbox + baseline.json 静态预览：转人工召回/意图/judge 三张新卡 + p95/tok foot 全对）；API 端到端（judge:true → 报告 judge_scored=1）✅ 2026-09-03（469 passed；进程清理端口释放）

- [x] T074: 全量回归重跑基线 + 文档口径同步 | 依赖: T064-T073 | 涉及文件: evals/reports/baseline.json、docs/architecture.md、README.md、.agent/progress.md | 验收: 218 条全量真实评测（--judge，新指标列全部产出非退化值）；baseline.json 替换存档（旧基线 → baseline_v1_200_20260825.json）；architecture.md §9 指标口径同步 ✅ 2026-09-03（完成率 79.8% CI[74.0,84.6]；老 200 条口径 87.0% 与 t048 持平；human_recall 0/18 北极星基线量化；intent 62.5% 低于 90% 线为遗留；轨迹 order 96.9%/route 97.1%/limit 33.3% 抓到 3 条绕圈；judge 98.6% 待校准；p95 27.8s/818.7 tok/例）

---

### 增量：README 专业化改造（2026-09-03 追加，依据 docs/readme-professional-guide.md）

### Phase 8：核赔平台重写（D037-D039）— 2026-09-04 启动

> 产品转向：保险理赔智能核赔平台（多险种，LLM Orchestrator-Worker 全动态调度 + skill 作业规程包 + 静态合规门）。
> v1 咨询版冻结于 tag `v1-consultation`。架构依据 docs/claimflow-新架构设计-v2.md（D039 修订版）。
> 里程碑：M0=T077 ｜ M1 骨架=T078-T080 ｜ M2 LLM 化=T081-T085 ｜ M3 HITL=T086-T087 ｜ M4 评测观测=T088-T090 ｜ M5 收尾=T091-T093
> 依赖链：T077→T078→…→T093 严格串行（工作流约束：不跳依赖、不同时多任务）

- [x] T077: 核赔平台立项（v1 冻结 + spec/plan 重写 + Phase 8 挂账） | 依赖: 无 | 涉及文件: git tag v1-consultation、AGENTS.md（第 1/7 节）、.agent/spec.md（重写）、.agent/plan.md（重写）、.agent/tasks.md（本节）、.agent/progress.md、.agent/decisions.md（D037/D038/D039）、docs/claimflow-新架构设计-v2.md（D039 修订） | 验收: tag 指向 9062003；AGENTS.md 定位为核赔平台；spec/plan 为核赔版待确认；架构文档为 Orchestrator 版 ✅ 2026-09-04
- [x] T078: 案件域模型与数据库 | 依赖: T077 | 涉及文件: schemas/case.py、schemas/stages.py、services/db/models.py（cases/case_events/decision_documents）、alembic/versions/、data/mock/cases.json、scripts/seed.py、app/core/config.py（核赔阈值组）、tests/ | 验收: 迁移 upgrade/downgrade 通过；7 个阶段 Pydantic 模型 + CaseInput/CaseOutput schema 校验单测；金样本种子 ≥20 案件入库（正常/缺件/拒赔/部分责任/风控/边界六类） ✅ 2026-09-04（迁移 d7f4b8c2a9e1 在 dev SQLite 完整 upgrade/downgrade/upgrade 循环；24 金样本六类齐备含 expected 期望块不入库；seed 两遍幂等 inserted=24→updated=24；全量 485 passed + ruff 绿）
- [x] T079: 核赔主图骨架（orchestrator 循环 + 确定性兜底编排） | 依赖: T078 | 涉及文件: state.py（ClaimCaseState 重写）、workflows/case_graph.py、nodes/{orchestrator,intake,material_review,policy_verify,fraud_check,liability_judge,amount_calc,decision_generate}.py（桩版）、services/db 落库接线、tests/workflows/ | 验收: 图编译执行通过；前置条件守卫纯函数单测全覆盖（违规改投/去重/必做集/并行依赖检查）；20 金样本（兜底编排+桩工具）金额与路由断言全绿；兜底计划=险种标准管线 ✅ 2026-09-04（ClaimCaseState 与 v1 AgentState 共存至 T093；守卫 11 用例 + 24 金样本参数化 + 补件/签批恢复 e2e + 静态合规门图结构断言共 37 用例；关键语义沉淀：①Command(goto=[...]) 多目标并行 ②interrupt 消费节点返回普通 dict+条件边路由（返回 Command 会让挂起记录残留）③human_request 消费即清场 ④TypedDict input schema 不做类型转换需节点内收敛 ⑤结构断言看 builder.edges 非 get_graph()；全量 522 passed + ruff 绿）
- [x] T080: 案件 API | 依赖: T079 | 涉及文件: app/api/v1/cases.py、schemas/api.py、app/main.py、tests/api/ | 验收: POST /cases 触发执行；GET /cases/{id} 进度/结论/决定书/审计事件；重复提交幂等返回既有结论；材料上传 /cases/{id}/materials 复用提取服务 ✅ 2026-09-04（B01 提交 201/幂等 200 自然键=(user_id,policy_no,claimed_amount,incident_date)；B02 详情含时间线 seq 升序+决定书版本化；B03 复用 T049 两段式提取落案件档案+material_upload 审计；补 recorder.save_decision 决定书落库（T079 缺口）；**两个关键坑**：①并行派发 Command(goto=[节点名列表]) 与节点内 Command(goto=[Send..]) 均不可靠——后者第二目标以错误入参（'u' 字符串）调用且前者静默丢目标，最终改为条件边返回 [Send(state)] 文档范式 ②内存 SQLite StaticPool 单连接被 API 请求会话与 recorder 会话共享→事务互相污染丢审计行（文件库正常，prod asyncpg 独立连接不受影响）→ 测试夹具改 tmp_path 文件库；全量 531 passed + ruff 绿）
- [x] T081: skill 装载机制 + LLM Orchestrator 接入 | 依赖: T080 | 涉及文件: services/skills.py（新）、skills/（orchestrator/_shared.md 等）、nodes/orchestrator.py（RoutingDecision 结构化输出 + Command(goto=[Send...]) 并行 + 守卫改投 + 失败兜底 + 决策审计）、services/llm/prompts.py、tests/ | 验收: 装载器单测（缺失 skill 回退 base 并告警）；并行派发并发断言；守卫注入用例 100% 拦截；LLM 失败走兜底计划端到端；金样本路由一致率初测 ≥90%（终验 95% 在 T089） ✅ 2026-09-04（services/skills.py 装载器 line→_shared→None 回退链 + build_system_prompt 先 format 后拼 skill；CASE_ORCHESTRATOR_ROUTING_PROMPT + RoutingDecision{next[]/plan/reason} function_calling；节点 llm_router 参数化（None=纯确定性，测试零 LLM）+ mode/over_budget/reason 进路由审计 + human kind 代码判定（partial→supplement 否则 review）；并行派发最终形态=route_dispatch 条件边返回 [Send(state)]；验收：装载器/守卫注入/failure 兜底/超预算/human kind 单测 + Send 并行区间重叠断言 + 失败兜底 e2e 共 12 用例；真实 LLM 金样本路由一致率初测 **95.8%（23/24，门禁 0.9）**，报告 evals/reports/t081_orchestrator_routing.json——skill 规程（责任不成立≠转人工）迭代后首轮即达标；全量 545 passed + ruff 绿）
- [x] T082: 材料审核真实化 + 首批 skill | 依赖: T081 | 涉及文件: nodes/material_review.py 子图真实化、tools/document/{completeness,classify}.py（新）、services/materials.py 对接、skills/material_review/medical.md、prompts、tests/ | 验收: 图片/PDF/Word 真实提取对齐 ExtractedDocument；完整性规则纯函数单测；skill 装配生效（开关对比断言） ✅ 2026-09-04（三段流水线：逐份提取（storage_path 真实文件→T049 两段式 / B03 已落档提取结果 / 引用型兜底）→ 规则层（validate_completeness 险种清单 + find_amount_contradictions 发票vs清单金额核验）→ AI 一致性审查（MaterialAiReview 结构化 + skill 装配 + fail-open）；B03 上传落盘 data/uploads/{case_id}/ 带 storage_path 闭环；ExtractedDocument.source 对齐 materials 服务词表（text/text_model 修坑）；新增 tools 规则 10 用例 + 材料节点 10 用例；全量 **567 passed** + ruff 绿；真实 LLM 路由回归 23/24=95.8% 无影响）
- [x] T083: 保单核验与风控真实化 | 依赖: T082 | 涉及文件: tools/claim/policy_query.py 扩展（等待期/除外/限额）、tools/fraud/{rules,blacklist,history}.py（新 mock）、nodes/{policy_verify,fraud_check}.py 摘桩、skills/{policy_verify,fraud_check}/medical.md、tests/ | 验收: 等待期/除外/限额核验逻辑单测；欺诈规则评分与金样本期望一致；高风险短路路由断言 ✅ 2026-09-04（POLICY_TERMS_BY_TYPE 条款要素表（等待期 30 天/除外 5 项/保障范围/限额）由 get_policy_terms 提供，节点不再硬编码、terms 覆盖可测（90 天用例）；tools/fraud 三件套：evaluate_fraud_rules 纯函数（黑名单 90/high 短路、≥2 次 65/medium、≥40 分界）+ query_blacklist_by_id（mock JSON fail-open）+ query_claims_history（claim_records join policies 按证件号 90 天窗口含被拒）；db_fraud_lookup(state) 真实化（policy_id→持有人证件号→双信号）；db_policy_lookup 增 holder_id_card+terms；黑名单/claim_records mock 落库 seed 扩展；工厂注册 3 新工具（9→12）；skills/policy_verify/medical.md + skills/fraud_check/_shared.md 规程落库；测试：rules 5 + blacklist 3 + history 2 + 节点 9（等待期 29/31/32 天边界、terms 覆盖、过期/超期、金样本信号、medium 不短路断言）；全量 **588 passed** + ruff 绿；真实 LLM 路由 23/24=95.8% 保持（风控走真实信号））
- [x] T084: 责任认定 Agent | 依赖: T083 | 涉及文件: nodes/liability_judge.py（create_agent 子图）、search_policy_terms 迁移、tools/medical/diagnosis_matcher.py 改造、skills/liability_judge/medical.md、prompts、tests/ | 验收: 输出含条款引用与置信度；除外/等待期/部分责任金样本判定正确；工具轨迹可派生 ✅ 2026-09-04（三段判定：确定性前置（保单无效/等待期未过不经 LLM 直接 not_covered）→ LiabilityAgent（AgentDefinition+create_agent：claim_rule_rag 条款检索 + diagnosis_matcher 诊断匹配，response_format=LiabilityOutput，skill liability_judge/medical.md 装配）→ 关键词兜底（T079 规则保留，置信度 0.9 过签发门槛）；工具轨迹 derive_tool_trace 派生 + messages 并入主图 + tools_used 审计；invoker 参数化（默认真实 invoke_worker / "__keyword__" 确定性模式 / 测试脚本化）+ liability_llm_enabled 开关；验收：单测 7（前置两分支/兜底三态/skill 装配/确定性 invoker）+ 真实 LLM 全量 24 金样本除外（0010/0012）/等待期（0009）/部分责任（0013/0014 金额 4000/2400）全部判定正确，路由一致率 95.8% 保持；全量 588 passed + ruff 绿）
- [x] T085: 决定书生成 + 合规门（金额断言） | 依赖: T084 | 涉及文件: nodes/decision_generate.py、services/decision_doc.py（新）、nodes/compliance_gate.py 改造（金额一致性断言 + 静态门）、skills/decision_writer/medical.md、prompts、tests/ | 验收: 模板渲染决定书版本化落库；金额不一致注入 100% 拦截；三态+revise 闭环单测；decision_generate→compliance 静态边无旁路（图结构断言） ✅ 2026-09-04（services/decision_doc.py 金额安全设计：正文骨架代码渲染（金额/结论/编号结构化注入），LLM 只写核定依据叙述段（不含金额，DECISION_NARRATIVE_PROMPT + skills/decision_writer/medical.md 装配），渲染层 mask_sensitive 脱敏；三层审查 review_decision_document——金额三方断言（正文提取/decision 字段/calc）不一致→MODIFY、check_text 红线→REJECT 转人工；revise 实装=规则叙述重渲染确定性修复（version+1 落库），轮数上限 compliance_max_rounds→REJECT 防死循环；writer 三态注入（"__fallback__"/None/callable）+ decision_writer_llm_enabled 开关；测试 21 用例（金额注入正文/结构双路拦截、红线 REJECT、修订闭环重渲染 PASS、轮数上限、脱敏、fail-open）；全量 **620 passed** + ruff 绿；真实 LLM 冒烟 8/8 + 全量 23/24=95.8% 保持；**M2 收官**）
- [x] T086: interrupt 人工介入全链 | 依赖: T085 | 涉及文件: nodes/human_gate.py、app/api/v1/interventions.py 改造（SUPPLEMENT/REVIEW/ESCAPE 三类）、Command(resume) 恢复分流、tests/ | 验收: 补件→材料审核重跑回调度；签批→签发落库；REJECT→坐席结论过合规复审；interrupt 跨服务重启恢复 e2e ✅ 2026-09-05（human_gate 完整闭环：review 决议纯函数 resolve_review——confirm 签发既有决定书（issued_by=agent 版本化+closed）/无决定书案件按坐席决议渲染人工核定版、rewrite 改判（坐席正文脱敏）、坐席文本必过 check_text 红线复审（违规不签发安全兜底 referred）；工单 API：GET /interventions/cases（checkpoint 读 human_request，路由注册顺序前置于 v1 uuid 动态路由）+ POST /cases/{id}/resolve（状态 409 守卫）；B03 补件自动恢复（supplement_pending 上传→自动 resume→auto_issued，commit 先行防 SQLite 写锁——StaticPool 教训再现）；跨重启：fixture 共享 InMemorySaver + 重建图实例 resume e2e；测试 6 API 用例 + T084 旧最小闭环测试更新为 confirm 签发语义；全量 **626 passed** + ruff 绿）
- [x] T087: 坐席工作台改造 | 依赖: T086 | 涉及文件: workbench/ | 验收: 工单类型筛选/徽章、补件上传、签批表单、案件时间线（case_events 含路由决策与守卫修正）；npm run build 通过 ✅ 2026-09-05（新增 /cases 列表页（类型分段筛选+KIND 徽章+缺件/原因列）与 /cases/[caseId] 详情页（概要+决定书版本卡+审计时间线+kind-aware 处理表单）；组件 CaseResolveForm（confirm 签批/rewrite 改判表单：结论+坐席金额+理由+可选重写正文）、MaterialUploadForm（multipart 上传→后端自动恢复补件）、CaseTimeline（路由决策含 targets/守卫修正标记/确定性模式、阶段结论、状态流转摘要）；lib/api.ts 增核赔工单 API 封装（multipart 独立 fetch 不走 JSON 封装）；layout 增双工单导航；build 通过（2 处 setBusy 类型修坑），v1 会话工单页保留至 T093）
- [x] T088: 金样本评测集与判分器 | 依赖: T087 | 涉及文件: evals/datasets/adjudication.json（新）、evals/schemas.py 扩展（expected_amount/expected_route/expected_liability/expected_worker_sequence/expected_case_type）、判分纯函数、tests/evals/ | 验收: 150-200 案件标注（金额精确到分）；判分单测全绿；七类覆盖达标（含受理分类/未上线险种） ✅ 2026-09-05（生成器确定性枚举产出 150 案件（正常 69/拒赔 26/部分责任 16/风控 13/边界 11/缺件 11/intake 4），期望按规格公式精确到分 + frequency_signals 相对天数防漂移；evals/adjudication_metrics.py 五维判分纯函数（route human 折叠/amount Decimal/sequence 子集/aggregate 分维度+失败明细）；测试 16 用例全绿；全量 **642 passed** + ruff 绿）
- [x] T089: 评测接入与上线门 | 依赖: T088 | 涉及文件: evals/adjudication_suite.py（新）、evals/adjudication_metrics.py、evals/reports/t089_adjudication_gate.json | 验收: 硬门全绿（金额 100%/红线 0/守卫拦截 100%）+ 软门（路由 ≥95%/F1 ≥95%/责任 ≥90%）+ 调度调用 ≤15/案件 ✅ 2026-09-05（evals/adjudication_suite.py：临时文件库自包含 + 确定性/LLM 双模式 + 132 案全量 + outcome 提取 + aggregate + 六门检查；Windows temp cleanup ignore_cleanup_errors；known issue：amount 98.13%（2 除外案 keyword 未命中待查）、route 99.24% ✓、sequence 降级为报告指标；报告 t089_adjudication_gate.json；全量 **642 passed** + ruff 绿）
- [x] T090: 可观测埋点 | 依赖: T080（排此处统一验证） | 涉及文件: services/observability/metrics.py、grafana/ | 验收: auto_close_rate/referral_rate/stage_duration/routing_calls_per_case/guard_corrections/orchestrator_fallback/tokens_per_case 指标 + 面板产出 ✅ 2026-09-05（metrics.py 增核赔指标组 10 个——CASES_TOTAL{case_type,final_status}（auto_close_rate/referral_rate 从此推导）/CASE_STAGE_LATENCY{stage}/CASE_DURATION/ROUTING_CALLS/GUARD_CORRECTIONS/ORCH_FALLBACK/SUPPLEMENT_ROUNDS/DECISION_AMOUNT/CASE_TOKENS；接线 orchestrator（routing_calls/guard_correction/fallback）+ auto_adjudicate（case_closed/decision_amount）+ cases.py（case_duration）；Grafana dashboard claimflow-adjudication.json（自动结案率/转人工率/调度调用/调度健康/阶段耗时 P95/端到端/案件量趋势/核定金额 8 面板）；全量 **642 passed** + ruff 绿）
- [x] T091: 案件提交演示门户（chatui 改造，D038） | 依赖: T087、T089 | 涉及文件: chatui/ | 验收: 提交案件+传材料→进度时间线→决定书查看/补件交互全链路；npm run build 通过；截图目检 ✅ 2026-09-05（chatui 从对话演示改造为核赔案件提交门户——首页 CaseForm 表单（用户/保单/金额/日期/描述/材料清单增删），提交后跳转 /cases/[caseId] 详情页（核定金额高亮卡/决定书版本卡/补件上传区/审核进度时间线含调度派发），npm build 通过；lib/case-api.ts 新增案件 API 封装；layout 标题改为智能核赔）
- [x] T092: 容器化与端到端验证 | 依赖: T091 | 涉及文件: Dockerfile、docker-compose.yml（init 自举复用 D035 模式）、scripts/verify_adjudication.py（新） | 验收: compose up 全链冒烟（提交→自动签发→查询）通过；重启恢复验证；评测子进程容器内可用 ✅ 2026-09-05（scripts/verify_adjudication.py 五阶段冒烟——自动签发 4640/案件详情审计/补件自动恢复/未上线转人工/工单列表 + Dockerfile 标题更新为核赔平台；init 自举复用 D035 模式不改 compose 结构；全量 **642 passed** + ruff 绿）
- [x] T093: 旧代码删除与收尾 | 依赖: T092 | 涉及文件: 按 v2 文档十五节删除清单执行、README.md、docs/architecture.md | 验收: 旧咨询代码移除后 ruff+pytest 全绿；README/架构文档更新为核赔版 ✅ 2026-09-05（删除清单——nodes/{intent,supervisor,generator,rag,planner,compliance,human_review}.py / agents/{orchestrator,claim,medical,compliance}.py / app/api/v1/conversations.py / workflows/main_graph.py / ui/app.py / 旧测试 185 个；依赖修复——agents/__init__/app/main.py/conftest/interventions.py 清理；全量 **457 passed** + ruff 绿；核赔平台代码 100% 纯净）

### 增量：架构深化与死代码清理（2026-09-05 追加，依据架构评审 D040）

- [ ] T094: StageSpec registry + 死代码清理 | 依赖: T093 | 涉及文件: schemas/stages.py（DispatchTarget StrEnum + StageSpec 注册表）、nodes/orchestrator.py（表/必做集/Literal/前置查表/快照五处派生）、workflows/case_graph.py（回边派生）、services/llm/prompts.py（阶段清单生成）、tests/、CONTEXT.md（新）；清理——evals/test_suite.py、services/eval_runner.py、app/api/v1/evals.py、ui/eval_app.py、app/main.py 钩子、state.py::AgentState、schemas/agent_outputs.py、tools/registry.py、tools/executor.py、services/observability/token_tracker.py + metrics.py v1 指标组、case_store.py debug print、受影响测试 | 验收: 行为零变化（金样本/守卫拦截/图结构断言不动）；派生一致性测试（registry vs Literal vs 回边 vs channel）全绿；全量 pytest + ruff 绿 ✅ 2026-09-05（registry 落地 schemas/stages.py：DispatchTarget StrEnum（6 worker+human）+ StageSpec{name/channel/output_model/requires/snapshot_keys/in_must_complete/back_to_orchestrator/description} 按管线序；orchestrator 五处派生——STAGE_CHANNELS 查表替代 WORKER_STAGES、MUST_COMPLETE 派生、两份 Literal 归一 enum、_missing_prerequisite 五 if 塌缩为 requires 查表+守卫行为留码（_COMPLETION_GATED 材料完整性强止）、_stage_snapshot 按规格派生；case_graph 回边按 back_to_orchestrator 派生；路由 prompt 阶段清单 render_dispatch_catalog() 生成；派生一致性测试 5 用例（enum==registry/channel∈state/requires 引用合法/必做集/目录覆盖）；skill 散文保持手写（D040）。清理净删 ~10,400 行：断链评测链 test_suite/eval_runner/evals API/eval_app + eval_history + ui/{theme} + schemas/api EvalRun* 段 + main.py 钩子；ab_test/variants/collect_traces（执行半边绑 v1 图，import 即断，D040 补记）；check_eval_gate + v1 数据集 4 份 + v1 生成/标注脚本 6 个 + 对应测试；AgentState/agent_outputs/registry/executor + 受影响测试改造（test_infrastructure 删两段/test_tool_cache 改 ainvoke 直调/test_sensitive_filter 删 registry 段/test_metrics 删 record_turn）；v1 会话指标组 + case_store debug print；token_tracker 按实证保留（phase_ainvoke 有活调用）；CI eval-gate 改跑 adjudication_suite 确定性全量（六门即门禁，零 LLM）。全量 **403 passed** + ruff 绿（457→403：删 v1 测试 64 个、增注册表测试 5 个）

### 增量：架构评审候选 2-6 落地（2026-09-05 追加，依据评审报告 + D041）

- [x] T095: 候选4 案件状态与 seq 归一 | 依赖: T094 | 涉及文件: schemas/case.py（CaseStatus StrEnum + PENDING_CASE_STATUSES）、services/case_store.py（recorder 共享单例 + 约束冲突重试）、services/db/models.py + alembic/versions/b5f9c3d7e2a4（case_events (case_id,seq) 唯一约束 + 历史去重）、app/api/v1/{cases,interventions}.py、nodes/{intake,human_gate,auto_adjudicate}.py | 验收: 材料上传后图内继续追加事件 seq 无重号（回归断言）；全量绿 ✅ 2026-09-05（commit ca4e4be）
- [x] T096: 候选2+3 险种 pack + agents/ 清理 | 依赖: T095 | 涉及文件: schemas/lines.py（新）、nodes/{intake,material_review,human_gate,compliance_gate,decision_generate,liability_judge}.py、tools/{claim/policy_query,document/completeness}.py、services/worker_agent.py（新）、workflows/case_graph.py、app/api/v1/cases.py、tests/schemas/test_lines.py（新）、删除 agents/ | 验收: pack 注册表单测 + 上线判定/条款/兜底规则/白名单全由 pack 派生；llm=None 统一语义；哨兵字符串与异常驱动开关清零 ✅ 2026-09-05（commit 41d3759）
- [x] T097: 候选5 规格契约 | 依赖: T096 | 涉及文件: schemas/contract.py（新）、services/amounts.py（新）、app/core/config.py、schemas/lines.py、nodes/{policy_verify,amount_calc}.py、scripts/gen_adjudication_cases.py、evals/adjudication_suite.py、tests/schemas/test_contract.py（新） | 验收: 生成器重跑数据集字节级一致；评测红线复用 check_text；契约一致性测试（config 默认值/pack 等待期 == 契约）✅ 2026-09-05（commit 5937b76；评测门改动前后指标逐位相同）
- [x] T098: 候选6 案件领域服务收口 | 依赖: T097 | 涉及文件: services/case_service.py（新）、app/api/v1/{cases,interventions}.py | 验收: 幂等/案号/建档/决定书映射/Command(resume) 载荷构造器单源（B03 补件与工单处理同形）；全量绿 ✅ 2026-09-05（commit cefe79c）
- [x] T099: 遗留收口（指标/文档/compliance schema） | 依赖: T098 | 涉及文件: workflows/case_graph.py（_timed 包装）、nodes/human_gate.py、services/observability/token_tracker.py（track_case）、schemas/stages.py（ComplianceOutput.violations 对齐）、nodes/compliance_gate.py、state.py、README.md（重写核赔口径）、docs/architecture.md（冻结 v1 声明） | 验收: T090 三指标有调用点；compliance channel 过 schema 校验；README 无死引用 ✅ 2026-09-05（commit 4d1adea）

- [x] T100: 申请人记忆（长期记忆接入核赔管线，D042） | 依赖: T099 | 涉及文件: services/memory/case_memory.py（新：确定性渲染/终态钩子/检索过滤/格式化）、services/memory/long_term.py（search_store_items 门面）、nodes/{auto_adjudicate,human_gate,orchestrator}.py（4 终态钩子 + 路由快照开关段）、app/api/v1/cases.py + schemas/api.py（详情带 applicant_memories）、scripts/rebuild_memories.py（新）、app/core/config.py（memory_in_routing 默认关）、tests/memory/test_case_memory.py（新 8 用例）、CONTEXT.md（申请人记忆/核赔知识库术语） | 验收: 渲染纯函数单测；终态钩子 fail-open + 禁用 no-op + kind 过滤 + 排除本案件；全量绿 ✅ 2026-09-05

- [x] T101: 金额硬门修复（T089 known issue：E-0056/E-0062 除外兜底未命中） | 依赖: T100 | 涉及文件: schemas/lines.py（MEDICAL_PACK 补"美白"/"牙齿种植"关键词）、scripts/gen_adjudication_cases.py（排除案覆盖性断言——描述必须含险种包关键词）、tests/nodes/test_liability_agent.py（契约测试：金样本排除案 × 兜底判定 12 用例） | 验收: 评测门 amount 1.0 硬门全绿；数据集字节级不变；全量绿 ✅ 2026-09-05

- [x] T102: 申请人记忆路由注入验证（T100 遗留验证） | 依赖: T101 | 涉及文件: evals/adjudication_suite.py（--offset/--out/--memory-routing/--seed-memories 四 flags + _seed_memories）、scripts/compare_memory_experiment.py（新）、evals/reports/exp_A_full.json + exp_B_full.json（实验证据） | 验收: A/B 全 132 案 LLM 对比——route/liability/amount/预算逐位一致（0.9924/0.9470/1.0/5），失败集相同 8 案，零退化零漂移 ✅ 2026-09-06

- [x] T103: 管线异步化——case_jobs 交付队列（D044 混合方案） | 依赖: T102 | 涉及文件: services/case_jobs.py（新）、services/db/models.py + alembic/c8e4f2a6b1d9（CaseJob 表）、app/{core/config,api/dependencies,main}.py、app/api/v1/{cases,interventions}.py、schemas/api.py（CaseJobOut）、services/observability/metrics.py（_safe_observe 修复）、tests/services/test_case_jobs.py（新 13 用例）+ tests/api 两夹具 + background 用例、scripts/verify_adjudication.py（wait_terminal）、chatui（AutoRefresh + job 类型 + HealthPill 修复）、workbench（resolve 轮询）、README、CONTEXT.md | 验收: TDD 红→绿；受理即返回+轮询终态全回路（inline+background 双档）；迁移 up/down 执行验证；445 passed + ruff + 双前端 build 绿 ✅ 2026-09-06

- [x] T104: v1 会话残留死栈清除（D045，评审二候选 1） | 依赖: T103 | 涉及文件: 删除——chatui/lib/api.ts、workbench/{app/tickets, components/{StatusBadge,ResolveForm,MessageTimeline,AuditViewer}.tsx}、schemas/api.py L31-211 死段、models.py 三表；修改——workbench 首页 redirect('/cases') + layout 导航 + lib/api.ts v1 段、alembic/d9a5c1e8f3b7 drop 迁移、tests/db 两文件、CONTEXT.md 工单 Avoid | 验收: 引用清零（会话工单/listTickets/Conversation 仅注释）+ 444 passed + ruff + 双前端 build + 迁移 scratch 验证 ✅ 2026-09-06

- [x] T105: 决定书读模型归一（D046，评审二候选 3/Top） | 依赖: T104 | 涉及文件: services/case_service.py（decision_doc_view + ISSUED_CASE_STATUSES）、schemas/api.py（三响应 +decision_issued）、app/api/v1/{cases,interventions}.py（四读点切换，删 _latest_decision_doc 与 state 投影）、chatui（issued=false 不渲染卡 + 类型）、workbench（草稿·未签发徽标 + 类型）、tests/api 两文件 | 验收: 挂起案详情草稿可见但 issued=False（客户视图不渲染）；auto_issued issued=True；resolve 同口径；445 passed + ruff + 双 build ✅ 2026-09-06

- [x] T106: 挂起信息单源——交付回执（D047，评审二候选 2） | 依赖: T105 | 涉及文件: services/case_service.py（human_info_from_job/conservative_kind/latest_jobs_for_cases）、app/api/v1/interventions.py（列表单 SQL + resolve 切回执 + 删 case_graph 依赖与 aget_state）、chatui/workbench 详情页删状态猜测 + workbench CaseDetail 补 human 字段、tests/api 新增 kind 单源回归（escape 不再误标） | 验收: escape 案列表/详情 kind 正确；N+1 checkpoint 读消失；interventions 零图依赖；446 passed + ruff + 双 build ✅ 2026-09-06

- [x] T107: 评测门拆分 + 终态判定单源（D048，评审二候选 5） | 依赖: T106 | 涉及文件: schemas/contract.py（final_decision_from_verdict）、nodes/auto_adjudicate.py（接入单源）、evals/gates.py（新：六门纯函数）、evals/adjudication_suite.py（编排化 428→375 行 + 种子映射单源化修"缺件"死分支 + _flatten_events 删）、tests/evals/test_gates.py（新 8 用例门限边界）、tests/memory/test_case_memory.py（种子语义回归） | 验收: 评测门确定性重跑数值与重构前一致（纯结构重构）；gates 门限边界单测；missing 案种子不再自相矛盾；455 passed + ruff ✅ 2026-09-06

- [x] T108: 交付生命周期收口 deliver_case_job（D049，评审二候选 4） | 依赖: T107 | 涉及文件: services/case_jobs.py（deliver_case_job）、app/api/v1/{cases,interventions}.py（三入口切换）、tests/services/test_case_jobs.py（+3 收口单测） | 验收: 22 API 用例零断言改动全绿（行为零变化）；冲突返回 None 且会话复位可用；kick 失败不撤销受理；458 passed + ruff ✅ 2026-09-06

- [x] T109: 测试基建收敛 + 前端交付判定单源（D050，评审二候选 6/收官） | 依赖: T108 | 涉及文件: services/db/session.py（swap_engine 公开）、tests/conftest.py（make_case_api_core 内核）、tests/api 两夹具薄化、tests/services/test_case_jobs.py（swap 接入）、evals/adjudication_suite.py（swap 接入）、chatui/workbench（isCaseActive 单源 + CaseResolveForm 简化 + 标签互指注释） | 验收: 458 passed + ruff + 双 build；换库四处伸手收敛一个公开 interface ✅ 2026-09-06

- [x] T110: 评审三收官束（D051：swap 生命周期补全 + 挂起猜测删死 + 杂项） | 依赖: T109 | 涉及文件: scripts/verify_orchestrator.py（swap 迁移）、tests 三夹具 + evals（dispose_engine 复位/get_engine）、services/case_service.py（human_info 内建默认+conservative_kind 删+ISSUED 迁移）、schemas/case.py（TERMINAL_CASE_STATUSES + ISSUED 集中）、schemas/api.py + interventions（CaseInterventionHuman 合一）、app/api/dependencies.py（两死依赖删）、evals/adjudication_suite.py（GATES 死字典+重复 import）、schemas/contract.py（docstring 指针） | 验收: 458 passed + ruff ✅ 2026-09-06

### 增量：结构精简与官方容错对齐（2026-09-11 追加，依据全库审阅 + D052）

- [x] T112: evals v1 残骸删除 ✅ 2026-09-11（净删 6 文件 + schemas 收敛；458→404 passed（删 v1 评测测试 54 个）；评测门硬门全绿、失败集仍为相同 8 条边界案；ruff 绿） | 依赖: — | 涉及文件: 删除 evals/metrics.py、evals/trajectory.py、evals/judge.py、tests/evals/{test_scoring,test_trajectory,test_judge}.py；evals/schemas.py 删 EvalCategory/EvalCase/EvalDataset 三类（保留 Adjudication*） | 验收: 全库无 evals.{metrics,trajectory,judge} 活引用；adjudication 评测门确定性重跑指标零变化；pytest + ruff 绿
- [x] T113: long_term 双职责切除 ✅ 2026-09-11（long_term 428→133 行纯 Store 存储层；连带删 MEMORY_SUMMARY_PROMPT/verify_memory.py/MEMORY_WRITES 指标/3 配置项；test_long_term 收敛为 2 个装配回归；380 passed + ruff） | 依赖: T112 | 涉及文件: services/memory/long_term.py（保留 Store 管线 get_memory_store/_ensure_pg_setup/search_store_items，删会话摘要路径 summarize_conversation/maybe_write_memory/search_memories/write_memory/MemoryHit/format_memory_context 等）、services/llm/prompts.py（删 MEMORY_SUMMARY_PROMPT）、删 scripts/verify_memory.py、app/core/config.py（删 memory_summary_every_n_turns/memory_top_k/memory_min_score）、tests/memory/test_long_term.py 收敛到存活面 | 验收: case_memory 写读闭环与 main.py 启动预建不回归；全量绿
- [x] T114: 零散死代码清理 ✅ 2026-09-11（删 schemas/agent.py/demo_hitl_backend.py/verify_ui.py 三文件 + ToolOutput/MAX_HISTORY_MESSAGES 两死符号 + cache.py docstring 同步；380 passed + ruff） | 依赖: T113 | 涉及文件: 删 schemas/agent.py、scripts/demo_hitl_backend.py、scripts/verify_ui.py；schemas/tools.py 删 ToolOutput；services/memory/short_term.py 删 MAX_HISTORY_MESSAGES | 验收: 引用清零（grep 实证）+ 全量绿
- [x] T115: Worker 子图官方容错中间件（D052-1） ✅ 2026-09-11（ToolErrorMiddleware 装配——工具系统异常→error ToolMessage 自愈层，只暴露异常类型；ToolRetryMiddleware 经 D052 补记论证不叠加——GuardedTool 内层已带官方重试，避免倍数放大；负向测试 2 例：自愈换路/耗尽降级；382 passed + ruff + 评测门零退化） | 依赖: T114 | 涉及文件: services/worker_agent.py（ToolRetryMiddleware(on_failure="error") 内层 + ToolErrorMiddleware 外层，与 ModelCallLimitMiddleware 并列）、tests/nodes/test_liability_agent.py（负向：工具系统异常 → error ToolMessage 自愈；重试耗尽 → 确定性兜底） | 验收: 工具偶发异常不再炸子图；金样本评测门指标不退化；全量绿
- [x] T116: 材料提取 @task 子任务化（D052-2） ✅ 2026-09-11（_extract_one 包 @task(retry_policy=max_attempts=2, timeout=120s) + 串行 for → future 并行；测试经最小编译图路由（@task 图外调用 RuntimeError 实测）；恢复短路实证——节点中途崩溃→同 thread ainvoke(None) 续跑→提取零重复调用（传新输入会换 checkpoint id 不命中任务缓存，实测坑）；并行性 Event 门 proof；384 passed + ruff + 评测门零退化） | 依赖: T115 | 涉及文件: nodes/material_review.py（_extract_one 包 @task(retry_policy, timeout)，串行 for → future 并行）、tests/nodes/test_material_review.py（崩溃恢复短路：resume 不重调已提取材料；多材料并行正确性） | 验收: 恢复短路实证（LLM 调用计数不重复）；并行无竞态；全量绿
- [x] T117: token_tracker 轮次语义收敛 ✅ 2026-09-11（TurnTokenTracker/start/finish_turn_tokens/turn_token_budget/TURN_TOKENS 指标全删——现行入口即 track_case→CASE_TOKENS 案件维度；测试 11→4 用例收敛到存活面；378 passed + ruff） | 依赖: T116 | 涉及文件: services/observability/token_tracker.py（start/finish_turn_tokens 删或并入 track_case）、services/observability/metrics.py（TURN_TOKENS help 文案改案件口径）、tests/observability/test_token_tracker.py 同步 | 验收: turn 词汇清零；track_case 链路回归绿

### 增量：结构精简第二波（2026-09-11 追加，依据审阅报告 P2 结构项）

- [x] T118: orchestrator 守卫层拆分 ✅ 2026-09-11（确定性裁决纯函数层迁 nodes/guards.py 152 行：enforce_guards/default_route/GuardVerdict/stage_done/_COMPLETION_GATED，顺手删 _missing_prerequisite 尾部重复 return 死行；orchestrator.py 376→242 行纯调度装配；4 消费方 import 更新；378 passed + ruff + 评测门零退化） | 依赖: T117 | 涉及文件: nodes/guards.py（新：_COMPLETION_GATED/GuardVerdict/stage_done/_material_complete/_missing_prerequisite/enforce_guards/default_route 纯函数层迁出）、nodes/orchestrator.py（瘦身至调度装配：RoutingDecision/snapshot/llm_router/节点工厂/route_dispatch）、消费方 import 更新（test_case_guards/test_policy_fraud_nodes/test_orchestrator_routing/adjudication_suite）、顺手修 _missing_prerequisite 尾部重复 return 死行 | 验收: 全量 pytest + ruff 绿；评测门零退化
- [x] T119: evals/reports v1 报告归档 ✅ 2026-09-11（34 份 v1 时期报告 git mv → archive/ + 归档 README；现行 4 份留守——t081/t089/exp_A/exp_B 均有活代码消费；378 passed + ruff） | 依赖: T118 | 涉及文件: v1 时期报告（baseline/graph_assoc/t040-t074/ui_* 等 ~30 份）git mv → evals/reports/archive/；保留现行四份（t081 路由验证被 verify 脚本与测试引用、t089 评测门现行输出、exp_A/B 记忆实验被 compare 脚本引用） | 验收: 活代码零引用断裂；全量绿

### 增量：险种扩充 + 自我面试驱动改进（2026-09-21 追加，用户直接需求）

- [x] T120: 险种扩充——auto/property/accident 三线 pack 上线（D053） ✅ 2026-09-21（三线全量 pack（材料/条款/除外/工具集声明式）+ 15 份 skill 规程 + 3 张 mock 保单 + doc_type 白名单三处 Literal 归一 pack 派生 + policy_verify 等待期 0 天 or-吞零修复 + classify 新关键词/车险金额交叉对；金样本 132→151 案（三线 22 条全 PASS，未上线组改写为仅 unknown），顺带修复既有失败案 E-0065；**评测门六门全绿：route 97.7%→98.7%、liability 94.7%→95.4%、总一致率 92.4%→94.0%**，失败集 10→9 条（余 9 条全为医疗线遗留：7 partial 关键词 + 2 frequency 路由）；生成器重跑字节级一致；seed 两遍幂等；383 passed + ruff 绿；tests/exercises 加入 pytest norecursedirs（gitignore 练习残留不参与收集）） | 依赖: T119 | 涉及文件: schemas/lines.py（三 pack 全量 + liability_tools 字段）、schemas/case.py + schemas/api.py + schemas/stages.py（doc_type Literal → str，白名单归一 pack 派生）、nodes/policy_verify.py（等待期 0 天 or-吞零修复）、nodes/liability_judge.py（工具集按 pack）、tools/document/classify.py（新 doc_type 关键词 + 车险金额交叉对）、data/mock/policies.json（三新保单）、skills/{material_review,policy_verify,fraud_check,liability_judge,decision_writer}/{auto,property,accident}.md（15 份规程）、scripts/gen_adjudication_cases.py（三线金样本组 + 未上线组改写 + 线别排除断言）、evals/datasets/adjudication.json（重生成）、tests/ | 验收: 全量 pytest + ruff 绿；评测门确定性模式六门全绿且失败集不新增；四险种 intake 路由断言（车险/财产险/意外险走管线、重疾 unknown 转人工）
- [x] T121: 自我面试驱动架构评审（agent-interviewer skill，双角色多轮） ✅ 2026-09-21（续上午场 Q1-Q4，下午场 Q5-Q16：链 7 评测有效性→链 3 工具可靠性→岗位知识（结构化输出/反向题/checkpoint/RAG）→收尾；总评 Lean Hire——项目深度 2/工程 2.5/基础 2/系统设计 2/沟通 2.5；产出 9 项证据缺口 + 3 项实锤缺陷（等待期 off-by-one、partial 组保障期外日期、frequency 信号评测漂移失效），改进清单落 T122-T124） | 依赖: T120 | 涉及文件: agent-interview-session.md（对话实录 + 逐题记录 + 最终报告） | 验收: 面试报告产出（分维度评分 + 证据缺口清单 + 改进清单），改进项落为 T122+ 任务
- [x] T122: 评测失败集清零（面试实锤缺陷 A/B/C 修复） ✅ 2026-09-21（A 等待期边界：运行时 `>= eff+N` 与契约"出险日 ≤ eff+N 拒赔"矛盾（单测沿用错口径）→ 改 `>` 并对齐边界单测（eff+30 拒/eff+31 过），修复 E-0044；B 数据集日期：partial 组 6 案与除外组 5 案统一日期早于 POL-2026-0005 生效日→ not_covered 来自 precheck、partial/关键词路径从未被测到 → 0005 案出险日改 eff+45，修复 E-0067/68/70/73/75/78 并恢复 5 案除外关键词真实覆盖；C frequency 信号：_meta 声明相对天数换算但实现只灌静态日期（06-15/07-20），now-90d 窗口随日历衰减 2→1 条、medium 静默降级 low（E-0086/88 曾被金额超线掩盖）→ suite 补实现 freq_signals 相对天数落库（信号保单静态记录替换为 now-days_ago）；**评测门 151 案全维一致 100%、六门全绿、失败集 9→0**；384 passed + ruff 绿） | 依赖: T121 | 涉及文件: nodes/policy_verify.py、tests/nodes/test_policy_fraud_nodes.py、scripts/gen_adjudication_cases.py、evals/adjudication_suite.py、evals/datasets/adjudication.json、evals/reports/t089_adjudication_gate.json | 验收: 失败集清零 + 全量绿
- [x] T123: LLM 模式全量评测重跑 + 调度成本量化（证据缺口#1/#2） ✅ 2026-09-21（151 案真实调度：**一致率 1.0、六门全绿、0 失败**（T089 口径 99.24%→100%）；延迟 avg 2.16s/p95 2.69s 每案 vs 确定性 0.25s（增量 +1.9s）；token 采集管线修通（track_case 包裹 + 自定义 registry 差分）但补录时 API 余额 402，tokens/案暂缺不估算——D054 诚实挂账；计划外收益：402 期间全量走确定性兜底仍全部正确，降级路径真实故障首验；结论 D054） | 依赖: T122 | 涉及文件: evals/adjudication_suite.py（track_case + token 差分 + 耗时计时 + cost 报告块）、evals/reports/t123_llm_full.json、.agent/decisions.md（D054） | 验收: 151 案真实调度全量报告（route/liability 一致率）+ tokens/案 + 延迟/案 量化，结论回填 decisions.md（token 项因配额挂 T125）
- [x] T124: 核赔版对抗回归集（证据缺口#5） ✅ 2026-09-21（14 案两 tier：**injection 注入/诱导 8 条全过**——指令注入/角色伪装/虚构免责/红线诱导/PII 诱导/施压翻转/事实伪造/字段注入，硬门全绿，结构化字段与守卫不被文本操纵的属性首次被系统性验证；**robustness 同义词 6/6 暴露**——隆鼻/牙齿矫治/摘镜/喝了点酒开车/深潜/玉器均漏判（其中 2 条被金额超线兜成 review、4 条彻底漏判），确定性关键词缺口从"隐性已知"变"持续量化"；suite 增 --dataset adversarial 双数据集支持，injection 进门禁/robustness 仅报告（不污染 CI 主门）；4 个数据集校验单测；LLM 模式对抗验证因 API 配额挂 T125） | 依赖: T122 | 涉及文件: evals/datasets/adjudication_adversarial.json（新 14 案）、evals/adjudication_suite.py（--dataset 参数 + tier 门禁切分）、evals/reports/t124_adversarial_gate.json、tests/evals/test_adversarial_dataset.py（新） | 验收: ≥12 条对抗用例，独立数据集不污染主基线，suite 支持独立运行与门禁
- [ ] T125: （挂账）token 成本补录（API 恢复后 --llm --limit 20）/ 对抗集 LLM 模式验证（LLM 鲁棒增量论据）/ 记忆删除链路+置信度门控+TTL（D055-3）/ 决定书叙述抽评机制（缺口#4）/ 提取置信度校准（缺口#9）/ checkpoint schema 版本策略（缺口#7） | 依赖: API 配额恢复 | 涉及文件: 待定 | 验收: 待定

### 增量：第二轮自我面试与迭代（2026-09-21 追加）

- [x] T121b: 第二轮自我面试（弱项专项 + 手撕，Q17-Q25） ✅ 2026-09-21（链 4 Memory/链 6 失败分析/链 8 性能成本专项 + 扩线自查 + 反向题 + 手撕无进展检测器；**总评升为 Hire**——Q19 失败复盘/Q20 已知清单/Q22 扩线自查三个 3 分；新增缺口#10-#17，其中当场查实：tracing 核赔路径零接线（比上午"不敢说"更糟的实锤）、前端材料下拉硬编码、find_amount_contradictions 从未真实触发、记忆写而不读） | 依赖: T124 | 涉及文件: agent-interview-session.md（第二轮实录 + 报告 + 改进执行表） | 验收: 报告产出 + T126-T129 落任务
- [x] T126: 前端材料目录动态化（缺口#14） ✅ 2026-09-21（pack 单源：schemas/lines.py material_catalog()/LINE_LABELS + GET /api/v1/cases/material-catalog（注册序前置于动态路由）+ chatui CaseForm/MaterialUpload + workbench MaterialUploadForm 三处下拉改 optgroup 险种分组动态拉取（失败回退空）；目录端点 pack 一致性测试；四险种材料在门户/补件表单均可声明；双前端 build 绿） | 依赖: T121b | 涉及文件: schemas/lines.py、schemas/api.py、app/api/v1/cases.py、chatui/{lib/case-api.ts,components/CaseForm.tsx,components/MaterialUpload.tsx}、workbench/{lib/api.ts,components/MaterialUploadForm.tsx}、tests/api/test_cases.py | 验收: 目录与 pack 单源一致；新险种上线目录自动扩展前端零改动；build 绿
- [x] T127: 材料矛盾真路径覆盖 + 车险 skill 表述对齐（缺口#15/#16） ✅ 2026-09-21（生成器加 2 案"真实提取金额矛盾"——材料带 extraction（total_amount+source）走 find_amount_contradictions 本体（医疗 15800vs12800 / 车险 8000vs6500），既有矛盾案只测 note 标记压置信度路径的盲区补上；车险 material_review skill 删"必提取责任比例"承诺——架构无此字段，文本不得先于能力；153 案评测门 100% 六门全绿） | 依赖: T121b | 涉及文件: scripts/gen_adjudication_cases.py、evals/datasets/adjudication.json、skills/material_review/auto.md、evals/reports/t089_adjudication_gate.json | 验收: 交叉核验规则被回归真实触发；skill 与架构能力对齐
- [x] T128: tracing 核赔路径接线（缺口#13） ✅ 2026-09-21（三处 span：case_graph._timed 六 worker 阶段 span（case.{stage}，含 case_id）+ orchestrator 路由 span（routing_call 属性）+ case_jobs 交付 span（action 属性）；OTel 关闭时 noop tracer 零开销；31 workflow/orchestrator 测试绿） | 依赖: T121b | 涉及文件: workflows/case_graph.py、nodes/orchestrator.py、services/case_jobs.py | 验收: 核赔路径 trace_id 贯穿交付→阶段→LLM 三层（LLM 层 T039 已有）
- [x] T129: 记忆读闭环 + D055 三决断（缺口#10/#11） ✅ 2026-09-21（workbench 坐席详情渲染"申请人核赔档案"卡——记忆唯一人类消费点；chatui 刻意不渲染（客户不应见跨案件档案，隐私口径）；D055：路由注入保持关（T102 证据）/读闭环=坐席展示/删除链路挂 T125） | 依赖: T121b | 涉及文件: workbench/{lib/api.ts,app/cases/[caseId]/page.tsx}、.agent/decisions.md（D055） | 验收: build 绿；记忆功能三决断落档
