# 需求文档 (Spec)

> Phase 1 产出。Agent 基于 PRD/用户描述填充本文件，用户确认后进入 Phase 2。
> 状态标记：✅ 已确认 | 🔄 待确认 | ❌ 需修改

**状态**：✅ 已确认（2026-08-24）

---

## 1. 问题陈述

保险理赔咨询往往需要跨系统联动（保单系统、医疗数据、理赔规则、合规风控），传统单轮 FAQ 机器人无法处理"我这个情况能赔多少？需要什么材料？"这类多步推理诉求，导致人工转接率高达 62%、平均处理时长 15 分钟。

本项目构建一个 **Orchestrator-Worker 模式的多智能体理赔对话系统**：由调度 Agent 理解意图并制定计划，指挥理赔核算 / 医疗审核 / 合规风控三个专精 Agent 通过工具调用完成跨系统查询与整合，所有输出经合规审查（一票否决）后返回，目标是端到端自动处理复杂理赔咨询，将人工转接率降至 37% 以下。

> 定位说明：本项目为求职作品集，但**按真实业务 PoC 原型的级别设计**——架构、容错、数据合规均按生产标准取舍，外部系统用可信 Mock 数据起步。

## 2. 目标用户

- **C 端投保人 / 被保险人**：咨询"能赔多少、需要什么材料、理赔进度、是否在保障范围"，期望一次对话得到整合后的准确答复
- **保险公司运营方（PoC 干系人）**：验证多 Agent 方案降低人工转接率（62% → ≤37%）、缩短处理时长（15min → ≤4min）的可行性
- **技术评审 / 面试官（作品集场景）**：通过可运行的演示与架构文档，评估多 Agent 编排、工具调用、合规风控的工程能力

## 3. MVP 功能列表

> 每条功能必须有可验证的验收标准。不在此列表中的功能不做。
> MVP 覆盖 Phase 0-2；Phase 1 先以单 Agent ReAct 跑通核心链路，Phase 2 拆分为多 Agent 协作。

| 编号 | 功能名称 | 描述 | 验收标准 |
|------|---------|------|---------|
| F01 | 项目脚手架与健康检查 | FastAPI + LangGraph + Docker Compose 骨架，PostgreSQL / Qdrant / Redis 一键启动 | `docker compose up` 后 `GET /health` 返回 200 且报告各依赖连接状态；本地 Docker 不可用时由 GitHub Actions CI 验证同样结果 |
| F02 | 对话 API | 创建会话、发送消息、获取回复的 REST 接口 | `POST /api/v1/conversations` 创建会话返回 ID；发消息后返回结构化回复（回答内容 + 使用的工具 + 合规状态） |
| F03 | 意图识别 | 将用户输入分类为：简单FAQ / 单领域查询 / 多步复杂任务 / 闲聊 / 其他 | 对 20 条预置测试语句，意图分类准确率 ≥ 90%；未知输入走兜底追问而非报错 |
| F04 | 工具层与保单查询（Mock） | BaseTool 接口 + 注册中心 + 执行器；`policy_query` 按保单号/身份证查询保单详情 | `policy_query("POL-2025-0001")` 返回预置保单数据（险种、保额、免赔额、生效日期）；不存在的保单号返回明确的 `success=false` 结构化错误 |
| F05 | 理赔计算器 | 按诊断、保额、免赔额、赔付比例计算预估赔付金额 | `claim_calculator` 对预置用例（如保额 100 万、免赔 1 万、比例 80%）计算结果正确，有单元测试覆盖 |
| F06 | 理赔规则 RAG | 10-20 篇理赔规则 markdown 文档入库（Qdrant + BGE-M3），检索免责条款、赔付比例、等待期规则 | 问"阑尾炎手术有等待期吗"能检索出相关条款片段并在回答中引用；检索结果按相似度排序返回 top-k |
| F07 | 单 Agent ReAct 核心流程（Phase 1 里程碑） | 单 Agent 对话循环：理解问题 → 选择工具 → 调用 → 整合回答 | 问"保单 POL-2025-0001 住院能赔多少"能自动调用 `policy_query` + `claim_calculator` 给出含金额的回答；对话界面可演示 |
| F08 | 多 Agent 协作 | Orchestrator 制定执行计划，调度 Claim / Medical Agent 按步执行，结果写入共享状态后整合 | 对多步任务（如"我做了阑尾炎手术能赔多少"）生成 ≥2 步计划并依次执行；每步的 Agent 调用记录可在响应/日志中追溯 |
| F09 | 医疗审核 Agent | 就诊记录查询（Mock）+ 诊断与 ICD-10 编码匹配 + 保障范围判断 | 输入诊断描述返回匹配的 ICD-10 编码及是否在保障范围内的结论；材料不齐全时明确列出缺失项 |
| F10 | 合规审查一票否决 | 所有输出必经 Compliance 节点，三态结果：PASS / MODIFY / REJECT | 构造含"保证赔付"话术的回答被 MODIFY 拦截并给出修改建议；高风险内容被 REJECT 后不返回用户、标记转人工；任何输出路径无法绕过合规节点（图结构保证） |
| F11 | 敏感信息脱敏 | 输出内容中身份证号、银行卡号、病历隐私自动脱敏 | 含 18 位身份证号的回答输出为 `3301**********1234` 形式；有单元测试覆盖各敏感字段模式 |
| F12 | OCR 图片材料上传（真实 OCR + Mock 兜底） | 上传诊断证明 / 发票图片，`deepseek-v4-flash-vision-exp` 提取结构化字段；vision API 失败时自动降级返回预置 Mock 数据 | `POST` 图片后返回结构化 JSON（姓名、诊断、金额、日期）及来源标记 `source: vision \| mock_fallback`；真实图片字段识别正确；vision API 异常时接口不报错、走 Mock 兜底；非图片文件返回 422 |
| F13 | 对话界面 | 基础 Web 对话界面（Streamlit / Gradio），支持发消息、看回复、上传图片 | 浏览器中完成"提问 → 工具调用 → 合规回答"完整演示；可上传图片触发 F12 |
| F14 | 对话状态持久化 | LangGraph Checkpoint（PostgreSQLSaver），会话中断可恢复 | 同一 conversation_id 多轮对话上下文连贯；服务重启后历史会话可继续 |

## 4. 非目标（明确不做什么）

- **长期记忆（跨会话用户历史向量记忆）**：后置到 Phase 3/4。Milvus 本身开源免费，"贵"在该功能的整体实现——写路径（何时摘要、存什么）、读路径（何时检索、如何注入 Prompt）、医疗数据长期存储的隐私合规策略，且核心演示链路不依赖它。MVP 期 Qdrant 仅承担 RAG 知识库检索（与长期记忆共用同一套 Qdrant 实例，后续扩展无需重复建设）
- **真实第三方系统对接**：保单系统、医疗系统为可信 Mock（OCR 已用 vision-exp 真实识别 + Mock 兜底，见 F12）——PoC 阶段无真实保单/医疗系统可用，接口抽象保留替换能力
- **人工介入工作台**：MVP 只标记 `need_human_intervention` 并输出预收集的上下文，不做坐席端界面与工单流转
- **监控体系（Prometheus / Grafana）**：Phase 3 实现；MVP 期仅输出结构化日志
- **完整评测体系与 A/B 框架**：Phase 3/4 实现；MVP 期仅保留意图分类的预置测试语句
- **GraphRAG / 合规风控模型优化**：Phase 4 深度亮点，不进 MVP
- **用户注册登录 / 多租户 / 权限体系**：PoC 场景单用户演示，不做鉴权
- **移动端适配**：仅提供桌面 Web 演示界面

## 5. 技术约束

- **语言**：Python 3.12，全量类型注解
- **Agent 框架**：LangGraph（状态机 + PostgreSQLSaver Checkpoint），主图定义于 `workflows/main_graph.py`，所有输出路径经 compliance 节点（条件边保证）
- **Web 框架**：FastAPI（async 风格），路由 RESTful 复数名词
- **数据**：PostgreSQL + SQLAlchemy 2.0 async；向量库 Qdrant（Embedding 用 BGE-M3；开发期用 local mode 本地文件模式零容器，交付用单容器部署，客户端代码不变）；缓存 Redis
- **LLM**：DeepSeek API（OpenAI 兼容接口，`base_url` + `api_key` + `model` 可配置切换其他供应商）；**混合策略（D008）**——主链路（意图/规划/工具调用/生成）用 `deepseek-v4-flash`，图片 OCR 专职用 `deepseek-v4-flash-vision-exp`（失败降级 Mock）；结构化输出用 JSON schema 约束
- **部署**：Docker + Docker Compose 一键启动；本地 Docker 因 WSL2/HCS 异常不可用时，容器化验证走 GitHub Actions CI（架构与 compose 文件按生产标准编写，不降级）
- **包管理**：uv（`pyproject.toml` + `uv.lock`）
- **配置**：pydantic-settings 从环境变量读取，敏感信息不硬编码，`.env.example` 列全
- **日志**：structlog 结构化日志，不 print
- **测试**：pytest + pytest-asyncio，核心逻辑（工具执行、状态流转、合规拦截、脱敏）必须有单元测试；外部调用一律 mock
- **工程约定**：每个任务独立 commit（`feat: T0XX ...`）、`.agent/` 状态文件同步更新、中文注释

## 6. 假设与依赖

- **假设**：DeepSeek API Key 由用户提供，开发期调用量可控（成本 <$10）
- **假设**：Mock 数据（保单、就诊记录、OCR 结果）由项目预置，参考真实理赔场景构造，覆盖正常/异常/边界案例
- **假设**：RAG 知识库文档（10-20 篇保险条款、理赔规则、免责说明）由项目自建，内容真实可信不侵权
- **外部依赖**：DeepSeek API 可用性（故障时系统降级为转人工提示，不阻塞演示）
- **外部依赖**：BGE-M3 Embedding 服务（本地部署或 API，Phase 2 接入 RAG 时确定部署形态）
- **环境依赖**：GitHub 仓库与 Actions 可用（CI 验证容器化）；本地开发期可用 `uv run` 直跑——Qdrant 用 local mode（本地文件、零容器），PostgreSQL/Redis 未就绪时提供 profile 降级（SQLite/MemorySaver、内存缓存），仅限开发调试，不影响交付架构
