# 任务清单 (Tasks)

> Phase 3 产出。基于 plan.md 拆解，用户确认后进入 Phase 4 逐个执行。
> 规则：1 个任务 = 1 个可独立验证的功能点 | 严格按顺序执行 | 不跳依赖
> 覆盖范围：MVP（spec F01-F14，对应 Phase 0-2）；Phase 3/4 任务不在本清单

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
- [ ] T018: 合规审查节点与三态流转 | 依赖: T017 | 涉及文件: nodes/compliance.py、tools/compliance/rule_check.py、tools/compliance/risk_scoring.py、workflows/main_graph.py | 验收: 含"保证赔付"话术的回答被 MODIFY 拦截并给修改建议；高风险内容 REJECT 后不返回用户、标记转人工；条件边保证所有输出路径必经合规节点（F10）
- [ ] T019: 敏感信息脱敏工具 | 依赖: T018 | 涉及文件: tools/compliance/sensitive_filter.py | 验收: 18 位身份证号输出 `3301**********1234` 格式；银行卡号/手机号脱敏；正则模式有单元测试覆盖（F11）
- [ ] T020: OCR 图片上传（真实 OCR + Mock 兜底） | 依赖: T018 | 涉及文件: app/api/v1/conversations.py（A07）、tools/medical/ocr_extract.py、data/mock/ocr_fallback.json、ui/app.py（上传组件） | 验收: 上传诊断证明图片返回结构化字段（姓名/诊断/金额/日期）+ `source: vision`；模拟 vision API 异常时返回 `source: mock_fallback` 且接口不报错；非图片文件 422（F12）
- [ ] T021: 主图组装与端到端联调 | 依赖: T018 | 涉及文件: workflows/main_graph.py（完整图：intent→分流→planner/step_executor/rag→compliance→generator/human_flag）、nodes/rag.py | 验收: A06 返回完整结构（answer/intent/used_tools/agent_steps/compliance_status/need_human_intervention）；多步任务全链路跑通；服务重启后历史会话可继续（F02 完整/F08/F14 完整）
- [ ] T022: 端到端测试与场景完善 | 依赖: T021 | 涉及文件: tests/（workflows/agents/api 全量）、data/mock/ | 验收: 核心链路单测 + API 集成测试（httpx AsyncClient）全绿；覆盖正常/异常/边界场景（保单不存在、LLM 超时、合规拦截、Mock 兜底）

### 交付

- [ ] T023: README 与最终验证 | 依赖: T022 | 涉及文件: README.md、.github/workflows/ci.yml | 验收: 对照 spec F01-F14 逐条核验通过；README 含安装/运行/API 文档/架构说明/CI 徽章；push 后 CI 全绿

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
```

## 进度统计

- 总任务数：23
- 已完成：17
- 进行中：0
- 待开始：6
