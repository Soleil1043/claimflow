# 决策记录 (Decisions)

> 全程产出。遇到需要决策的技术选型、架构取舍，记录在此，只追加不删除。
> 格式：编号 | 日期 | 决策内容 | 选项对比 | 最终选择 | 理由

---

## 决策条目

<!-- 每条记录格式：
## D0XX: [决策标题] — YYYY-MM-DD

**背景**：
[为什么需要做这个决策]

**选项**：
| 选项 | 优点 | 缺点 |
|------|------|------|
| A: [选项A] | [优点] | [缺点] |
| B: [选项B] | [优点] | [缺点] |

**最终选择**：[选项X]

**理由**：
[1-2 句话说明为什么选这个]

**影响**：
[这个决策影响哪些模块/任务]
-->

## D001: 向量数据库从 Milvus 切换为 Qdrant — 2026-08-24

**背景**：
spec 确认阶段复审向量库选型。项目为 PoC 级求职作品集，RAG 知识库仅 10-20 篇文档（千级以下向量）；开发者本地 Docker 因 WSL2/HCS 异常不可用，容器化验证依赖 GitHub Actions CI。原架构（ADR-003）选 Milvus。

**选项**：
| 选项 | 优点 | 缺点 |
|------|------|------|
| A: 维持 Milvus | 分布式成熟、亿级向量、中文社区活跃 | standalone 需 etcd+MinIO+Milvus 三容器（4GB+ 内存）；本地 Docker 不可用导致开发阻塞 |
| B: 切换 Qdrant | 单容器轻量；支持 local mode 零容器开发；API 简洁 | 英文社区为主；单机规模上限低于 Milvus（本项目用不到） |

**最终选择**：B（Qdrant）

**理由**：
规模严重错配——千级向量用不到 Milvus 的分布式能力，却要为它付 3 容器运维成本；Qdrant local mode 让本地开发完全摆脱容器依赖，开发与生产共用同一套客户端代码。

**影响**：
spec.md 技术约束、architecture.md（选型表 / 6.1 / 6.2 / ADR-003 标注取代 / 新增 ADR-004）、AGENTS.md 技术栈表；后续 compose 文件、RAG 服务层（services/rag/）实现。

## D002: LLM 供应商确定 DeepSeek — 2026-08-24

**背景**：
spec 阶段用户确认。统一走 OpenAI 兼容接口。

**最终选择**：DeepSeek API（`langchain-openai` 的 `ChatOpenAI` 指向 DeepSeek `base_url`）

**理由**：
国内直连无代理、成本低适合高频开发调试、function calling 能力满足多 Agent 工具调用需求；`base_url` + `api_key` 均走配置，可随时切换其他 OpenAI 兼容供应商。

**影响**：
services/llm/client.py、app/core/config.py、.env.example。

## D003: Embedding 用本地 sentence-transformers 跑 BGE-M3 — 2026-08-24

**背景**：
RAG 需要 BGE-M3（1024 维）做向量化，部署形态有本地模型 / 外部 API 两种。

**选项**：
| 选项 | 优点 | 缺点 |
|------|------|------|
| A: 本地 sentence-transformers | 无外部依赖、无网络抖动、数据不出境 | 首次下载模型 ~2GB，CPU 推理较慢（文档量小可接受） |
| B: Embedding API（如 SiliconFlow） | 无本地资源占用 | 外部依赖、Key 管理、开发调试受网络影响 |

**最终选择**：A（本地 sentence-transformers）

**理由**：
知识库仅 10-20 篇文档，入库一次性 + 查询低频，CPU 推理延迟可接受；去除外部依赖让演示链路更稳。

**影响**：
services/rag/embedder.py；Dockerfile 需考虑模型缓存层。

## D004: 演示界面用 Gradio — 2026-08-24

**背景**：
F13 需要基础对话界面，候选 Streamlit / Gradio。

**最终选择**：Gradio

**理由**：
`gr.ChatInterface` 对话界面开箱即用，多模态文件上传（F12 OCR）原生支持，单文件即可启动，适合演示场景。

**影响**：
ui/app.py（演示入口，不进核心架构）。

## D005: 开发期 profile 降级策略 — 2026-08-24

**背景**：
本地 Docker 不可用，但交付架构必须保持 PostgreSQL + Qdrant + Redis 原样（用户确认"坚持原架构"）。

**最终选择**：
`APP_PROFILE=dev|prod` 配置开关：dev 下 Qdrant 走 local mode、PostgreSQL 降级 SQLite(aiosqlite)+MemorySaver、Redis 降级内存 dict；prod/交付下全部真实依赖。同一代码路径，仅配置切换。

**理由**：
既不阻塞本地开发，又不让降级实现渗入交付架构；compose 与生产代码按原标准编写。

**影响**：
app/core/config.py、services/db/session.py、services/rag/retriever.py。

## D006: messages 表与 LangGraph checkpoint 并存 — 2026-08-24

**背景**：
LangGraph 自带 PostgreSQLSaver checkpoint 已持久化状态机消息，是否还需要业务侧 messages 表。

**最终选择**：
并存——checkpoint 服务状态机恢复（内部格式），messages 表服务对外 API 展示与审计追溯（含 tool_trace、compliance_status 业务字段）。

**理由**：
checkpoint 表结构由框架管理不宜对外查询；业务审计需要带语义的结构化记录（哪个 Agent、调了什么工具、合规结论）。

**影响**：
services/db/models.py、app/api/v1/conversations.py。

## D007: LLM 模型确定为 deepseek-v4-flash — 2026-08-24

**背景**：
用户提议使用 DeepSeek-V4-Flash。核实（2026-08-24）：正式版 `DeepSeek-V4-Flash-0731` 已于 2026-07-31 上线公测，284B 总参 / 13B 激活 MoE、1M 上下文、384K 最大输出；**旧别名 `deepseek-chat` / `deepseek-reasoner` 已于 2026-07-24 退役，调用直接报错**，新接入必须使用 `deepseek-v4-flash` 或 `deepseek-v4-pro`。

**选项**：
| 选项 | 优点 | 缺点 |
|------|------|------|
| A: deepseek-v4-flash | 官方约 1 元/百万输入（缓存命中 0.02 元）、2 元/百万输出；0731 版 Agent/工具调用能力大幅增强（官方称 9 项 agentic 基准超 V4-Pro 预览版）；1M 上下文 | 单条推理上限弱于 Pro |
| B: deepseek-v4-pro | 更强推理 | 价格约为 Flash 的 3 倍，开发调试高频调用不划算；Responses API 支持滞后 |

**最终选择**：A（`deepseek-v4-flash`）

**理由**：
本项目以工具调用编排为主（意图识别 / 规划 / 结构化输出），Flash 的 agentic 能力足够且成本极低；配置化 model 字段保留随时升级 Pro 的能力。

**影响**：
services/llm/client.py、app/core/config.py、.env.example（`LLM_MODEL=deepseek-v4-flash`）。

**备注（Phase 4 候选亮点）**：
2026-08-21 DeepSeek 开放多模态视觉模型 `deepseek-v4-flash-vision-exp`（图片单张最多折算 384 token，Files API 免费复用），可将 F12 Mock OCR 升级为真实 OCR，不进 MVP。

## D008: LLM 混合模型策略（flash 主链路 + vision-exp 专职 OCR） — 2026-08-24

**背景**：
用户提议直接使用 `deepseek-v4-flash-vision-exp`。核实官方文档：vision-exp 文本能力与 flash 正式版持平、价格相同、支持 Tool Calls / JSON Output，但官方定位为实验预览版，不建议直接用于生产。

**选项**：
| 选项 | 优点 | 缺点 |
|------|------|------|
| A: 混合策略 | 主链路用正式版稳定；vision-exp 真实 OCR 成亮点；风险隔离 | 两个模型配置项 |
| B: 全 vision-exp | 配置最简 | 95% 调用押注实验模型，模型调整则全链路受影响 |
| C: 维持纯 Mock OCR | 最稳 | 无多模态亮点 |

**最终选择**：A（混合策略）

**理由**：
主链路（意图/规划/工具调用/生成）占绝大多数调用量，用正式版保证演示稳定；OCR 是低频单点场景，vision-exp 失败自动降级 Mock 兜底（接口不报错）；与架构 6.3"分级模型"成本策略自洽。

**影响**：
spec.md F12 / 技术约束、plan.md 选型表 / A07、architecture.md 选型表 + ADR-005；services/llm/client.py（双模型封装，`LLM_MODEL` + `LLM_VISION_MODEL` 两个配置项）；tools/medical/ocr_extract.py（vision 调用 + Mock 兜底 + `source` 来源标记）。

## D009: checkpoint 依赖修正（langgraph-checkpoint-postgres + psycopg[binary]） — 2026-08-24

**背景**：
T001 执行 `uv sync` 时发现 plan.md 中包名 `langgraph-checkpoint-postgresql` 在 PyPI 不存在。

**修正**：
1. 正确包名为 `langgraph-checkpoint-postgres`（3.1.2，2026-08-07 发布），提供 `PostgresSaver` / `AsyncPostgresSaver`（注意：类名无 "QL"，与 AGENTS.md/plan.md 中写的 "PostgreSQLSaver" 不同，后续 T011 按实际 API 使用）
2. 该包默认依赖纯 Python psycopg，Windows 无系统 libpq 会 `ImportError: no pq wrapper available`，需显式添加 `psycopg[binary,pool]`（官方文档同样推荐）

**最终依赖**：
`langgraph-checkpoint-postgres>=3.1` + `psycopg[binary,pool]>=3.2`；业务表仍用 SQLAlchemy 2.0 async + asyncpg（两套驱动并存：checkpoint 走 psycopg，业务 ORM 走 asyncpg）

**影响**：
pyproject.toml；T011 checkpoint 接入时的 import 路径（`from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver`）；plan.md 第 5 节依赖表按此为准。

## D010: PyPI 源统一切换阿里云 + torch CPU 源 — 2026-08-24

**背景**：
T005 容器构建时发现两个网络/体积问题：① 容器内 files.pythonhosted.org 直连仅 ~50KB/s（16MB 轮子需 5 分钟，构建停滞）；② lock 中 torch 从 PyPI 解析，Linux 下拉 CUDA 依赖使镜像膨胀 2-3GB，而 Embedding 仅用 CPU。另 ghcr.io（astral-sh/uv 镜像）国内不可达。

**选项**：
| 选项 | 优点 | 缺点 |
|------|------|------|
| A: 官方源 + GPU torch | 与上游一致 | 容器构建停滞不可用；镜像巨大 |
| B: 阿里云 PyPI + PyTorch CPU 源（阿里云 flat 镜像） | host/容器/CI 全场景满速；torch CPU 轮子 183MB；lock 全量指向阿里云 | 依赖第三方镜像同步及时性 |

**最终选择**：B

**理由**：
阿里云 pypi/simple 与 pytorch-wheels/cpu 均实测满速（torch 183MB 18s）；`[[tool.uv.index]] default=true` + torch 显式直接依赖（uv source 仅作用于直接依赖，传递依赖不生效——关键坑）使 lock 中 771 个 URL 全部指向 mirrors.aliyun.com。

**影响**：
pyproject.toml [tool.uv]；uv.lock（146→128 包，移除 CUDA/triton）；Dockerfile（pip 亦走阿里云装 uv）。

## D011: Docker Hub 国内访问策略 — 2026-08-24

**背景**：
本机 Docker Hub 直连不可达（auth.docker.io 超时）；且发现 registry-mirrors 仅代理拉取层，dockerd 在 manifest/attestation 校验、token 认证时仍会回源官方域——postgres:16-alpine 拉取时触发回源，TCP 黑洞导致 dockerd 挂起，docker-desktop WSL2 VM 静默崩溃（backend 无 crash 记录、系统日志无 Hyper-V 错误，属本机 WSL2 偶发不稳定）。

**措施**：
1. 宿主机 daemon.json 配置 3 个镜像加速（docker.m.daocloud.io / docker.1ms.run / hub.rat.dev）——注意必须无 BOM 写入，UTF-8 BOM 会导致 Docker 引擎启动崩溃
2. `docker logout` 消除 Docker Desktop 对 hub.docker.com 的周期性登录检查
3. 镜像 tag 固定 + 全量本地预热，compose up 全部本地命中
4. CI（GitHub Actions）作为云端权威验证，规避国内网络限制

**影响**：
本机环境（不在仓库内）；CI 流程；后续新增镜像时先 `docker pull` 预热再 up。

## D012: 合规审查节点的实现路径 — 2026-08-25

**背景**：
T018 要求合规审查具备强可靠性（F10：违规内容必须被拦截，LLM 故障不能导致漏放行）。可选方案：
（A）复用 run_worker_agent 让 Compliance Agent 走 ReAct 工具循环；
（B）节点内先跑确定性规则工具取证，再单次 LLM 裁决，LLM 失败走确定性兜底判定。

**选项与理由**：
- A：与 Worker 路径一致，但输出解析失败会降级为 {"summary": ...}，丢失 verdict 三态——合规门禁不可接受
- B：规则工具（正则）结果确定性可得，兜底判定（FRAUD_RISK 或 risk≥80 → REJECT；其他违规 → MODIFY；无违规 → PASS）不依赖 LLM

**最终选择**：B。合规工具层同时提供纯函数（check_text / score_risk）与 BaseTool 封装：节点经 ToolExecutor 调用工具（未注册/执行失败时回退纯函数，保证拦截能力恒在）；BaseTool 版本注册供后续 Agent 路径复用。

**附带决策**：
- MODIFY 修订走 revise 节点（LLM 重写 + 正则兜底）→ 回 compliance 复审，compliance_rounds 上限 2 防死循环
- REJECT 在节点内直接替换 final_answer 为安全话术（违规内容不落审计、不返回用户），need_human_intervention=True，会话状态标记 human_intervention
- 合规工具调用不计入 tool_trace（used_tools 语义 = 本轮业务工具，合规是系统级门禁）

**影响**：
nodes/compliance.py、tools/compliance/、workflows/main_graph.py、A06 响应与审计。

## D013: 项目更名 claim-agent → claimflow — 2026-08-25

**背景**：
项目原名 claim-agent 与 LangGraph 架构内部的 Claim Agent（理赔核算 Agent）撞名——"项目级"与"组件级"的 Agent 概念混淆，面试/作品集场景易引起误解。

**候选**：
- claim-orchestrator：直接呼应 Orchestrator-Worker 架构，稍长
- claimflow：简短好记，强调理赔全流程编排（意图→规划→执行→合规）
- claimcrew：暗示多智能体团队，但与 CrewAI 框架有联想
- claimmate：助手定位，不体现多智能体架构

**最终选择**：claimflow（用户确认）。风格与用户另一项目 toutiao-news 一致（小写+连字符）。

**改动范围**：
pyproject.toml root 包名 + uv.lock 重新生成；ci.yml 镜像名；app/main.py FastAPI title；README 标题/badge/clone URL；GitHub 仓库名；本地目录名。数据库名 claim_agent 为数据层内部标识（POSTGRES_DB），不影响外部认知，保持不动。历史记录（progress/decisions）按只追加原则不改。

## D014: 评测数据集按架构完整规模 200 条构建 — 2026-08-25

**背景**：
Phase 3 规划时提出评测集规模取舍（架构 9.2 规划 200 条 vs 演示项目精简 40/80 条）。

**候选**：
- 精简 40 条：标注成本低，但覆盖度弱，评测报告说服力不足
- 中间 80 条：折中
- 完整 200 条：严格对齐架构文档 9.2 规划比例（FAQ 30 / 单领域 60 / 多步复杂 80 / 边界异常 30）

**最终选择**：完整 200 条（用户确认）。作为求职作品集，评测规模本身是工程化能力的展示点；用例可基于已有 Mock 数据体系程序化辅助生成 + 人工校验，控制标注成本。

## D015: OpenTelemetry 全链路追踪后置 Phase 4 — 2026-08-25

**背景**：
架构 8.2 提到 OTel Trace，Phase 3 立项时评估是否纳入。

**候选**：
- 纳入 Phase 3：指标 + 追踪一次到位，但需引入 OTel SDK + Jaeger 后端容器，任务数 +2
- 后置 Phase 4：Phase 3 聚焦 Prometheus 指标 + Grafana 面板；现有结构化日志已含每轮对话完整执行轨迹（工具入参/出参/耗时），可观测性基本盘已够

**最终选择**：后置 Phase 4（用户确认）。OTel + Jaeger 与 GraphRAG、A/B 测试同属"深度亮点"层，放 Phase 4 更聚焦。

## D016: 监控栈用独立 compose profile（monitoring）— 2026-08-25

**背景**：
T025 实现方式取舍：监控服务是否默认随 `docker compose up` 启动。

**候选**：
- A. 默认启动：一键全栈，但 CI docker job 也拉起 Prometheus/Grafana，多拉两个镜像变慢，监控非业务可用必要条件
- B. 独立 profile `monitoring`：`docker compose --profile monitoring up -d` 显式启动；CI 与常规启动不变；语义清晰（监控是可选增强）

**最终选择**：B（用户确认）。`docker compose config --services` 双向验证：默认 4 服务，带 profile 6 服务。

## D017: Phase 4 范围与 GraphRAG 轻量自建路径 — 2026-08-25

**背景**：
Phase 4 立项规划：架构文档规划了 GraphRAG / 合规风控模型优化 / A/B 测试 / 人工介入工作台四项，需结合现状与求职方向（Agent 全栈 / Agent 应用工程师）取舍。

**候选与理由**：
- GraphRAG（纳入，3 任务）：架构 Phase 4 核心规划；与普通 RAG 形成差异化对比，是面试叙事最强项
- 长期记忆（纳入，2 任务）：架构 6.1 三层记忆已设计未实现，补齐即闭环
- OTel + Jaeger（纳入，压缩为 1 任务）：D015 后置项；LangSmith 讨论结论（当前场景必要性 10%，OTel 优先于 LangSmith：开源/自托管/无锁定）
- A/B 实验框架（纳入，2 任务）：复用 T027 评测运行器，T039 用 deepseek-v4-pro 跑 200 条对比（预算 ¥5-10 用户确认）
- 合规风控模型优化（不纳入）：规则引擎已达标（评测红线违规 0/200），优化方向模糊
- 总量 12 任务（用户确认）

**GraphRAG 实现路径——轻量自建**：
- LLM 从 12 篇 kb_docs 抽取实体关系三元组（险种/疾病/等待期/免赔/免责），内存图结构 + 落盘复用
- 与现有 Qdrant 向量检索做混合召回（图邻接扩展 + 向量检索融合重排）
- 否决 LightRAG/nano-graphrag：新依赖且可控性低；否决 Neo4j：重容器，演示规模过度设计

## D018: 人工介入工作台按精简方案 B 实现 — 2026-08-25

**背景**：
架构 Phase 4 规划"人工介入工作台"，评估必要性随求职方向变化：纯后端 15% / Agent 应用工程师 40-45% / **Agent 全栈 70-75%**。用户目标方向为 Agent 全栈或 Agent 应用工程师。

**候选**：
- A. 全量工作台（WebSocket 实时推单 + 完整坐席系统，4-5 任务）：叙事满分但项目膨胀
- B. 精简工作台（3 任务）：Next.js 前端（转人工会话列表 + 上下文详情 + 处理动作）+ 后端工单 API + LangGraph interrupt 恢复机制
- C. 纯后端 HITL（1 任务）：工单状态机 + REST API，无 UI
- D. 不做

**最终选择**：B（用户确认）。理由：
1. 前端复用 toutiao-news 技能栈（Next.js 15 + React 19 + Tailwind，技能是热的），边际成本≈2-3 天
2. 后端复用现有审计数据：messages 表 tool_trace/agent_steps/compliance_status 字段本来就是为"给坐席看上下文"设计，前端只做渲染，只需 3 个只读 API + 1 个状态更新
3. 成为唯一"AI 语境下产品级前端"作品：claimflow 补前端短板，toutiao-news 补 AI 短板，双证互补
4. HITL 后端模式（LangGraph interrupt/Command 恢复）同时满足 Agent 应用工程师叙事

## D019: A/B 实战实验结论——主链路维持 deepseek-v4-flash，glm-5.3-flash 作跨供应商备选 — 2026-08-27

**背景**：
T041 要求 200 条全量 A/B 对比产出选型结论。原计划对比 deepseek-v4-pro，实测其思考型输出（reasoning tokens 计费）单例耗时 2-3 倍、成本约 Flash 3 倍，跑到 28/200 时用户决定对比组切换为 glm-5.3-flash（智谱）——实验性质从同厂升级档位对比变为**跨供应商对比**，正好实证架构"供应商经 OpenAI 兼容接口配置切换"的设计目标（D002）。

**实验数据（evals/reports/t041_glm_20260827_082238，各 200 条全量）**：
| 维度 | deepseek-v4-flash（基线） | glm-5.3-flash | 差异 |
|------|--------------------------|---------------|------|
| 任务完成率 | 90.5%（181/200） | 89.5%（179/200） | -1.0pp，z=0.333 **不显著** |
| 工具调用准确率 | 96.3% | 95.8% | -0.5pp，z=0.263 **不显著** |
| 合规通过率 | 99.5% | 100% | 红线违规均为 0 |
| 平均耗时 | 21.2s | 107.5s | **含 30+ 次 429 限流退避**，不可作纯推理速度解读 |
| token 消耗 | 1.76M | 2.03M（+15.4%） | glm 表述风格更冗长 |

失败分布：glm 的 21 条失败中 14 条与基线共同失败（判分词面严格的历史问题，POL-\* 聚簇），
仅 7 条为 glm 独有（基线也有 5 条独有失败）——双侧差异主要是 LLM 表述随机波动而非能力差距。
分类上 glm 在 multi_step 略优（77/80 vs 76/80）、edge 略差（26/30 vs 28/30）。

**最终选择**：主链路维持 deepseek-v4-flash；glm-5.3-flash 注册为跨供应商容灾备选变体。

**理由**：
1. 质量维度两者统计等价（差距 <1pp 且 z 检验均不显著），切换供应商不构成质量收益
2. glm 在本 Key 档位存在明显速率限制（串行评测即持续触发 429），生产并发场景会放大；
   基线 DeepSeek 全程零限流零失败
3. token +15% 意味着同等价卡下 glm 成本略高（两家 flash 档单价均极低，绝对值都在预算内）
4. 保留 glm-5.3-flash 变体（evals/variants.py，$ 字段间接引用 Key 不进代码）作为
   DeepSeek 故障/停服时的容灾切换目标——配置三行即可切换，风险已被本实验量化

**影响**：
T042 收尾叙事（跨供应商可迁移性实证）；生产部署建议增加"LLM 供应商健康探测 + 一键切换"的运维预案（不在本项目范围）。

## D020: 重排序选型——排除 Qwen3-Reranker，落地 bge-reranker-v2-m3 可开关精排层 — 2026-08-27

**背景**：
用户调研掘金《主流开源 Rerank 模型解析与选型指南（2026 版）》后提议增加重排序、选型 Qwen3-Reranker 系列。按文章自身决策树对照 claimflow 画像分析（分析结论：不建议 ~75-80%），用户拍板折中方案：落地 bge-reranker-v2-m3 可开关精排层 + 真实评测验证。

**选项对比（按文章决策树逐条对照）**：
| 维度 | Qwen3-Reranker-4B | Qwen3-Reranker-0.6B | bge-reranker-v2-m3（567M） |
|------|------|------|------|
| 部署 | FP16 14GB 显存，**本项目纯 CPU 不可行** | CPU 可跑但 LLM 式打分延迟更高 | CPU 可跑，INT8 量化 <200MB（后续路径） |
| 生态 | 独立 prompt 格式，非 ST CrossEncoder | 同左 | **与 BGE-M3 同生态，ST CrossEncoder 开箱即用** |
| 候选规模匹配 | 候选 <50 场景文章指向轻量型 | 同左 | 契合 |
| 卖点匹配 | 32K 长文本——本项目 chunk ≤800 字，卖点用不上 | — | — |

**最终选择**：bge-reranker-v2-m3 + 可开关精排层（`RERANK_ENABLED` 默认关）：
rag_node top-8 召回 → CrossEncoder 重排 → top-4；失败回退向量序零影响。

**实测数据（真实模型 + simple_faq 30 条 ×2 A/B，evals/reports/t043_rerank_20260827_135845）**：
- 任务完成率 93.3% → 96.7%（+3.3pp，z=-0.592 **不显著**，n=30）；FAQ-021 翻转为 PASS
- 检索质量：top1 五类标准查询 0/5 变化（向量序已对）；**次序去重改善**——"理赔需要什么材料"
  基线 top4 混 3 条重复的"进度查询"，精排把 FAQ 材料条目顶上来
- 延迟：重排 1.3s/查询（torch fp32 CPU，8 候选）；端到端 +0.9s（7.8→8.7s，+12%）
- token +3.3%；模型加载 11s（惰性，仅开启时）

**结论**：
小语料（53 chunk）下重排序增益真实存在但幅度小（次序去重 > top1 修正，完成率不显著），
默认关符合 T033"语料扩大后再评估"的既定结论；**Qwen3-Reranker 系列正式排除**
（4B 硬件不可行、0.6B 在本项目 CPU + CrossEncoder 生态下全面劣于 bge）。语料扩到千级
chunk 或接入 GPU 时重新评估，届时优先验证 onnx-int8 后端（服务已留开关位，需另装
optimum/onnxruntime 并导出量化模型）。

**影响**：
nodes/rag.py（召回-精排两段式）、services/rag/reranker.py、配置 RERANK_* 六项、
variants rerank_off/rerank_on、architecture.md 技术选型表重排序行更新为已落地。

## D021: 架构 v2 全面对齐 LangGraph/LangChain 标准构件（supervisor 化 + 文档先行）— 2026-09-01

**背景**：
评审发现 v1 的 Agent/工具层偏离 LangGraph/LangChain 官方模式：自研 BaseTool（信封/注册中心/集中执行器）、
AgentDefinition + 两处手写 ReAct 循环、四个 LLM 决策点手写 JSON 解析；而图编排层（StateGraph/条件边/
checkpoint/interrupt）本身已是官方标准。用户指示以 LangGraph 图结构为基础重新设计架构，原则：
**所有部分尽量按 LangGraph/LangChain 已定义的方法、类、架构实施，非必要不增加自定义内容**。

**选项**：
1. C 维持现状 + 补决策记录说明偏离理由——成本最低，但不满足"对齐官方"诉求
2. A 温和对齐：保留 plan-execute 调度，仅工具换 langchain BaseTool、Worker 换 create_react_agent——
   改动小，但调度层仍非官方范式
3. B 全面原生化：supervisor 动态路由（Command(goto)）+ create_react_agent 子图 + ToolNode +
   with_structured_output + 官方 Store 记忆——最贴官方文档，改动大

**最终选择**：B，且**文档先行**（v2 设计写入 architecture.md，代码零改动，迁移任务 T044-T048 待确认后执行）。
两个关键子决策由用户拍板：① 多步调度采用 supervisor 动态路由（planner + step_executor 游标循环并入
supervisor 节点，支持执行中重规划）；② 本次仅交付文档与状态记录。

**仅保留自研清单**（框架无对应物，ADR-007 逐项论证必要性）：熔断器、工具结果缓存白名单、
领域工具/Prompt/各降级规则。**删除项**：自研 BaseTool 与 ToolOutput 信封、ToolRegistry、
ToolExecutor 集中层、AgentDefinition、两处手写 ReAct 循环、四处手写 JSON 解析、
tool_trace/agent_steps 状态字段、phase_ainvoke 包装、Qdrant 长期记忆管线（迁官方 Store）。

**影响**：
docs/architecture.md（v2 重设计，ADR-007）、.agent/tasks.md（Phase 5：T044-T048）、
AGENTS.md 6.1/6.2（与 v2 冲突，T044 前置修订）、pyproject.toml（langgraph 下限收紧，
create_react_agent 的 prompt/response_format 需较新 0.2.x）。
风险控制：200 条评测基线回归，任务完成率相对基线 89.5% 回退 ≤1pp 为验收线。

## D022: v2 设计 API 全量验证——create_react_agent 已废弃，改选 langchain.agents.create_agent — 2026-09-01

**背景**：
D021 完成后用户要求：先查官方文档，再全量验证 plan.md / architecture.md 的 API 写法，
保证符合 uv.lock 实锁版本（langgraph 1.2.11 / langgraph-prebuilt 1.1.0 / langchain-core 1.6.0 /
langchain-openai 1.6.0 / langgraph-checkpoint-postgres 3.1.2）。验证方式：官方文档（docs.langchain.com）
+ .venv 实装源码签名双重核对。

**验证通过（无需改动）**：
`Command(goto/update/resume)`、`interrupt`、`ToolNode(handle_tool_errors)`、`tools_condition`、
`with_structured_output`、`Runnable.with_retry / with_fallbacks`、`bind_tools`、
`BaseTool.args_schema + _arun`（model_fields 确认）、`InMemorySaver` / `AsyncPostgresSaver`、
`InMemoryStore(index=IndexConfig{dims,embed,fields})`、`response_format → structured_response` 状态键。

**发现并修正（4 项）**：
1. `convert_to_openai_tool` 已从 langchain-core 1.x 移除——architecture.md §4.1 删除该提法（只保留 bind_tools）
2. `AsyncPostgresStore` 实际路径 `langgraph.store.postgres`（langgraph 主包提供、复用 psycopg），
   非 langgraph-checkpoint-postgres——§6 注记修正
3. **`langgraph.prebuilt.create_react_agent` 自 LangGraph 1.0 起标记 @deprecated**
   （源码确认，官方文档指向 `langchain.agents.create_agent` 为现行标准）
4. 官方文档 1.x 主推 `@tool` 装饰器定义工具（docstring=描述、类型注解=schema、
   `@tool(args_schema=...)`、`runtime: ToolRuntime` 上下文注入）——§4.1 改为 @tool 主推、
   BaseTool 子类作有状态（依赖注入）备选

**最终选择**：v2 Worker/单领域子图构造器改用 `langchain.agents.create_agent`
（system_prompt 静态 + 动态任务指令经输入 messages 注入 + response_format → structured_response +
middleware 钩子体系），需新增 `langchain>=1.0` 依赖（列入 T044）。
备选（不采用）：已装的 create_react_agent——deprecated 但 langgraph-prebuilt 1.1.0 完整可用，
若坚持零新增依赖可退回此选项。

**plan.md 历史遗留修正（3 处）**：`PostgreSQLSaver` 类名 ×2 → PostgresSaver/AsyncPostgresSaver（D009
口径）；包名 `langgraph-checkpoint-postgresql` → `langgraph-checkpoint-postgres`；依赖表更新为 uv.lock
实锁版本。

**影响**：
docs/architecture.md（§2.4/§4.1/§5.3/§6/§11/ADR-007 验证补记/文档状态头）、.agent/plan.md（§1/§3/§5）、
.agent/tasks.md（T044 新增 langchain 依赖 + @tool、T046/T047 改 create_agent）。
实施时仍须以实装源码签名为准（inspect.signature 复核），2026 年后续版本演进不在此验证范围内。

## D023: 意图分类 multi_step 更名为 complex_consult — 2026-09-01

**背景**：
用户指出 `multi_step` 命名描述的是**解法**（要执行多步）而非**问题本身**，与同组其他四个意图
（simple_faq / single_domain / chitchat / other，均为问题视角）分类轴不一致；且"几步"应是
supervisor 运行时决策，不应烙进意图分类。

**选项**：
1. `multi_intent`（复合诉求）——NLU 标准术语，名字即判据（数诉求个数），标注一致性最好（模型推荐）
2. `cross_domain`（跨领域咨询）——与 single_domain 同轴对称最干净，但"单领域多诉求"会被误名
3. `complex_consult`（复杂理赔咨询）——业务语义直白、宽容度最大（跨域/多诉求/多步推理全兜住），
   边界相对模糊需靠 few-shot 定义

**最终选择**：`complex_consult`（用户拍板）。

**影响**：
- v2 设计即时同步：architecture.md §5.2/§5.3、docs/diagrams/agent_flow_v2.mmd
- 实施放 T045（本就要重写意图模块）：VALID_INTENTS / INTENT_CLASSIFICATION_PROMPT few-shot /
  关键词兜底规则 / route_intent；20 条意图测试集与 200 条评测集标注批量替换；
  messages 表历史 intent 值读取时做旧值映射（multi_step → complex_consult）保持 API/审计连续；
  A06 返回新值
- v1 代码与 README 现行实现不动（T045 前 multi_step 仍为实际生效值）

## D024: 材料上传支持 PDF 与 Word（两段式提取 + 兼容别名端点）— 2026-09-01

**背景**：
用户要求上传材料必须支持 PDF/Word——真实理赔场景中诊断证明/病历常为 PDF 或 Word 而非图片。
现状 A07 仅收 png/jpeg/webp/bmp，其余一律 422。

**选项**：
1. 全走 vision 渲染——PDF 可渲染成图，Word 无法可靠渲染，且文本型材料走视觉浪费成本
2. 两段式分派：PDF 先 `pypdf` 抽文本（文本型），文本不足（扫描件）→ `pypdfium2` 渲染前 N 页
   走既有 vision OCR；Word(.docx) 用 `python-docx` 抽正文+表格 → 主链路 flash 模型结构化提取
   （新增文本版 prompt，输出 schema 与图片版一致）
3. 引入重型文档解析框架（unstructured 等）——依赖重，项目量级不需要

**最终选择**：2。文本型材料走主链路模型与 D008 分级模型策略自洽（文本任务不占 vision）；
扫描件才走 vision；`.doc` 旧格式不支持（422 明确提示转存 .docx）。

**护栏与兼容**：
- 大小上限 `MATERIAL_MAX_SIZE_MB`（默认 10MB）；扫描件渲染页数 `MATERIAL_PDF_RENDER_PAGES`（默认 3）；
  PDF 文本 < `MATERIAL_PDF_TEXT_MIN_CHARS`（默认 50 字符）判为扫描件
- 端点新增规范路径 `POST /materials`，`/images` 保留为兼容别名（同一处理函数，双路由装饰器）
- 任何提取失败 → 既有 Mock 兜底（source=mock_fallback），接口不报错（D008 语义延续）
- 响应体新增 `file_type`（image/pdf/docx）

**影响**：
pyproject（+pypdf/pypdfium2/python-docx）、app/core/config.py、.env.example、
services/llm/prompts.py（OCR_EXTRACT_TEXT_PROMPT）、services/materials.py（新）、
app/api/v1/conversations.py（A07 泛化）、schemas/api.py、ui/app.py（文件选择器+端点）、
tests/、plan.md A07 行、README。任务编号 T049（独立于 Phase 5 的用户直接需求）。

## D025: PDF 识别技术栈定位——材料提取维持「两段式 + API VLM」，RAG 知识库摄入预留 Docling — 2026-09-01

**背景**：
用户调研 2026 年 PDF 解析技术栈全景（VLM-first：Docling / Marker / GOT-OCR；传统版面分析：
Unstructured / MinerU / PyMuPDF+pdfplumber；商业 API：LlamaParse / Firecrawl / Reducto；
多阶段流水线趋势），要求据此确定本项目 PDF 识别选型。

**场景区分（选型关键）**：
1. A07 材料提取（T049）：1-3 页诊断证明/病历/发票 → 4 个结构化字段，非版面忠实转换任务
2. RAG 知识库摄入（潜在）：条款 PDF → 高质量 Markdown/结构化 chunk（当前 KB 为手写 markdown，
   暂无此需求；语料扩大到真实条款 PDF 时启动）

**决策**：
- A07 维持 D024 两段式。趋势「Vision-First 替代传统流水线」本项目已采用：扫描件版面理解
  直接委托 vision VLM（deepseek-vision-exp），等价于合并多阶段流水线的 Layout Detection +
  Multimodal Extraction；API VLM 而非本地 VLM 是 CPU-only 约束下的正确形态
- RAG 知识库摄入 PDF 时选 **Docling**：CPU 可跑（torch 已在依赖树）、MIT、面向 RAG 的
  Markdown/JSON 输出与结构化分块、LF AI 活跃维护——开源方案中唯一同时满足全部约束者

**排除理由**：
- Marker/Surya、MinerU、GOT-OCR 2.0：GPU 面向，纯 CPU 延迟/体积不可接受
- Unstructured.io：Windows 系统依赖（libmagic 等）痛苦，dev 环境为 Windows
- PyMuPDF：AGPL 许可，保险场景商用隐患（pypdfium2 为 BSD/Apache 已选）
- LlamaParse / Firecrawl / Reducto：理赔材料含身份证/病历 PII，不新增第三方数据出境通道，
  与 D003 本地优先原则及保险合规叙事冲突

**影响**：T049 按现实现收尾；知识库 PDF 摄入立项时按本决策选 Docling（开新任务，不进 T049）。

## D026: 轨迹质量评测独立口径——暂不并入 passed — 2026-09-02

**背景**：
现行评测只考核"调没调对工具"（expected_tools 集合子集匹配），轨迹维度（调用顺序 / 冗余 /
Agent 路由 / 入参正确性）不可见。多步任务的 ReAct 绕圈、乱序、参数幻觉是后续 prompt 调优的
主要观察对象，需要可量化的轨迹指标；但轨迹数据集标注刚起步，标注覆盖率与准确性未经验证。

**选项**：
1. 轨迹分并入 passed——历史曲线断档（baseline.json 88% 为旧口径），标注不全时全量回归被
   新维度绑架，T048 验收结论失去可比基准
2. 独立报告口径——新指标单列进报告，passed 五维判分不变；待标注稳定后随数据集 version bump
   再决策是否并入
3. 单独 trajectory 数据集——用例与期望割裂，同题两处维护

**最终选择**：选项 2（用户 2026-09-02 拍板"轨迹暂时不列入 passed"）。

**关键口径**：
- 顺序匹配 = 期望序列是实际序列的按序子序列（LCS 占比 == 1.0）：容忍合理插入调用、惩罚乱序
- 路由序列从 task_plan 派生（全局去重保序）——Worker 子图消息不带 agent 名，messages 派生不可靠
- CaseResult.tool_trace 只存 {agent, tool, input} 摘要（output 不入库控报告体积；检索命中统计
  在 result_from_a06 内消费完整 output，口径不变）
- simple_faq 的 rag_node 直检路径补记一条隐式 claim_rule_rag 轨迹（与 used_tools 补记口径一致）
- 未标注维度不考核（分母不计入），与 expected_tools 为空不考核的既有原则一致

**影响**：
T050 落地（schemas/trajectory/metrics/test_suite/数据集抽样标注/单测）；A/B 对比 JSON 的
variant_summaries 自动携带新指标（aggregate 聚合层透传），MD 表格暂不加列；
是否并入 passed 待轨迹标注稳定一至两个版本后另立决策。

## D027: 评测 UI——子进程隔离执行 + 独立 Gradio 页 + 轮询 — 2026-09-02

**背景**：
用户要求给评测做 UI：手动点击按钮开始评测并查看结果。评测是分钟级长任务（10 条冒烟 ~2min，
200 条全量 ~40min），需要后台执行 + 进度反馈 + 结果查看三件套。

**选项（执行形态）**：
1. API 进程内直接跑 eval 循环——evals.test_suite 会重建主图并 close 全局 checkpointer/
   嵌入单例，与 API 服务的常驻单例互相污染；LLM 长调用挤占服务事件循环
2. **子进程复用 CLI**——`python -m evals.test_suite ...` 独立进程执行，预热/守卫/落盘语义
   全部复用，API 侧只做生命周期管理与 stdout 进度解析（选定）
3. Celery/消息队列——当前单机演示规模杀鸡用牛刀

**选项（UI 形态）**：独立 Gradio 页（ui/eval_app.py，端口 7861）而非聊天 demo 加 Tab——
与 T014 演示解耦、可分离部署，与既有"ui 走 HTTP 调 FastAPI"架构一致；进度用 gr.Timer 轮询
（2s）而非 SSE，Gradio 6 内建 timer 足够，不引入额外通道。

**决策**：执行=子进程隔离（D027），UI=独立 Gradio 页 + 轮询；单活跃运行守卫（并发启动 409，
评测独占嵌入模型与 API 配额）；运行注册表存内存（磁盘报告 JSON 本身即持久历史，不重复建表）。

**影响**：
T051 落地（services/eval_runner.py + app/api/v1/evals.py + ui/eval_app.py + 单测）；
评测报告落盘 evals/reports/ui_<run_id>.json，与 CLI 报告同目录共存。

## D028: 评测运行历史持久化——单写者两阶段 + fail-open + git_sha — 2026-09-02

**背景**：T051 运行注册表在内存，服务重启即失；失败运行（无报告产出）与运行↔代码版本绑定
完全丢失。评测的核心工作流是回归对比，"这个数字是哪个代码版本跑出来的"必须可回答。

**决策**：
- **单写者**：UI 托管运行由 EvalRunManager 两阶段写库（start=running / finish=终态）；
  CLI 运行由 evals.test_suite 进程收尾自记；子进程环境注入 `EVAL_MANAGED_BY=api` 时跳过
  自记——同一行永远只有一个写者，杜绝双写
- **fail-open**：历史写入/查询失败只 warn，绝不影响评测本身（评测产物是报告，历史是附属）
- **git_sha 进历史也进报告**：报告 JSON 与 eval_runs 表均记录 `git rev-parse --short HEAD`
  （缓存，git 不可用回退 unknown），每个数字可复现
- **存储**：复用既有 SQLAlchemy async（dev=SQLite / prod=PostgreSQL）加 eval_runs 表；
  完成率/工具准确率冗余成数值列（SQLite 无 JSON 查询能力，趋势查询直接排序过滤）

**影响**：T052 落地；趋势图（T053）以本表为主要数据源。

## D029: 趋势图数据源双合并 + plotly 选型 — 2026-09-02

**背景**：T052 前的存量报告（baseline.json、t0xx 系列等 20+ 份）没有历史行；趋势图若只读
eval_runs 表会丢掉全部历史。

**决策**：
- /trends 双来源合并：DB 历史行 + reports 目录文件扫描，按 report_name 去重（DB 行优先），
  时间升序输出；每点带 source 标签（db/report）便于甄别
- 绘图选 **plotly**（新增依赖）：gr.Plot 原生支持、hover 交互（点级 run 标签/变体/commit）
  是趋势图的核心可读性需求，matplotlib 静态图无法承载；点量级 <10² 性能无忧

**影响**：T053 落地；pyproject 增 plotly 直接依赖。

## D030: 三界面设计系统——Apple Design 移植 Web + 双栈令牌同源 — 2026-09-02

**背景**：
三个界面（Gradio 聊天 7860 / Gradio 评测台 7861 / Next.js 坐席工作台 3000）均为功能优先的
默认样式：Gradio 裸主题、工作台基础 Tailwind，无统一设计语言。用户要求按 Apple Design
方法论（WWDC Designing Fluid Interfaces / UI Typography / 八项设计原则）优化重构。

**选项**：
1. 各界面各自美化——快但三界面三套风格，后续改设计要改三处
2. **双栈令牌同源**——Python 侧 ui/theme.py（Gradio theme + 共享 CSS）与 Next 侧
   globals.css（Tailwind v4 @theme 变量 + 工具类）定义同名同值令牌，各自消费（选定）
3. 全量换框架（如聊天/评测台也改 Next.js）——收益不抵重写成本，Gradio 承载回调已稳定

**决策**：
- 设计语言：Apple 系统调色板（#007AFF/#34C759/#FF9500/#FF3B30 + #F5F5F7 底 + #1D1D1F 文）、
  系统字体栈 + 尺寸分级 tracking、半透明浮层 chrome（backdrop-filter）、连续圆角近似、
  :active scale(0.97)/100ms 按压反馈、cubic-bezier(0.32,0.72,0,1) 标准缓动、
  prefers-reduced-motion/reduced-transparency 降级
- 硬约束：纯表现层——不改 API 契约 / 业务逻辑 / Gradio 回调元数（T053 教训：poll 8 输出锁定）
- 取舍：Web 端不做手势弹簧物理（无拖拽场景），取 Apple 设计中的静态可移植部分
  （排版/材质/色彩/反馈/微动效/无障碍），手势相关原则不适用范围不强行套用

**影响**：
T054-T058 落地（ui/theme.py + 两 Gradio 应用重构 + workbench 全站重构 + 主题单测）；
README 增设计系统章节；后续新 UI 一律消费既有令牌，不再散装样式。

---

## D031：Gradio 双界面视觉深化（impeccable 方法论，2026-09-02）

**背景**：Phase 6（D030）落地后用户反馈仅 Next.js 坐席工作台达到预期；两个 Gradio 界面受默认 DOM/样式限制视觉偏平（单层阴影、无动效层次、区块无节奏）。

**决策**：按 impeccable 技能方法论对 ui/theme.py 共享层与两应用做深化。约束不变：纯表现层、Gradio 回调元数锁定（poll 8 输出）、双栈令牌同源契约不破坏。

- 深度层次：分层阴影（--cf-shadow-1/2/3）替代单层；卡片表面微渐变（#FFFFFF→#FBFBFD）；页面顶部极淡蓝色 radial wash——拒绝「纯灰死板背景」（tinted neutral 原则）
- 动效：入场 cf-rise（420ms，ease-out-quint cubic-bezier(0.22,1,0.36,1)，交错延迟 60/120ms）；KPI 卡 hover 升起 -2px；warn 状态点脉冲；全部走 prefers-reduced-motion 降级；poll 高频更新区（状态卡）禁用入场动画防 2s 重放
- 排版：区块标题体系（cf-kicker 11px 大写 0.1em + 蓝色短划线 + cf-h2 19px/-0.015em），数字一律 tabular-nums
- 细节：::selection 蓝色选区、自定义细滚动条、隐藏 Gradio footer、placeholder 对比度 #86868B、表格行 hover、输入焦点光环（3px 蓝晕）、primary 按钮 hover 升起
- 评测台结构：趋势/历史报告改 gr.Tabs 分区（组件对象引用不变）；分类明细表加 mini 进度条（≥80% 绿 / ≥60% 橙 / 其余红语义配色）
- 演示界面：头部品牌 logo 块（渐变蓝方块 CF）+ 状态 pill 化，与 workbench 导航品牌语言对齐

**新增变量仅作派生令牌**（--cf-shadow-1/2/3、--cf-ease-out、--cf-blue-soft 等），TOKENS 核心值不动 → workbench @theme 同名同值契约不受影响。

**影响**：T059-T062；tests/ui/test_theme.py 新增 polish 断言锁死关键选择器。

---

## D032: 对话界面用 Next.js 重写（chatui/），评测台保留 Gradio — 2026-09-03

**背景**：
用户反馈两个 Gradio 界面（演示/评测台）的观感与交互不如 Next.js 坐席工作台，询问是否 Gradio 框架所致。分析结论：Gradio 只能通过外部 CSS 覆盖其生成的 DOM（ui/theme.py 579 行本质是"撬皮肤"，改不了 DOM 骨架与交互形态，T062 宽屏居中/移动端前缀两轮修复即为与框架搏斗的证据）；坐席工作台是手写前端，DOM/布局/交互全量可控。用户选定方案 2：对话界面重写，评测台保留。

**选项**：
| 选项 | 优点 | 缺点 |
|------|------|------|
| A: 继续打磨 Gradio theme.py | 零新依赖、改动小 | 天花板已见：DOM 骨架不可控，覆盖式 CSS 在框架升级时易碎 |
| B: 对话界面 Next.js 重写，共享 workbench 设计系统 | 全量 DOM/交互控制；A06 富数据（intent/agent_steps/compliance/used_tools）可结构化展示而非拍平成 markdown 脚注；复用既有设计令牌与 rewrites 代理模式 | 新增一个前端应用与一份 @theme 令牌拷贝（三栈同源） |
| C: 演示+评测台全部迁移 Next.js | 技术栈完全统一 | 评测台轮询/趋势/历史报告需全部自研，工作量大；其为内部工具收益低 |

**最终选择**：B（独立应用 `chatui/`，Next.js 15 + React 19 + Tailwind 4，端口 3000）

**理由**：
对话界面是面向演示的第一门面，值得产品级 UI；评测台是内部工具，Gradio 够用。选择独立应用而非 workbench 加路由：演示界面与内部坐席工具的部署形态/受众不同，保持可分离部署（与 ui/app.py「ui 与 app 可分离部署」承诺一致）。

**影响**：
- 新增 `chatui/` 应用；`chatui/app/globals.css` 的 @theme 块与 workbench 同名同值（双栈同源契约扩展为三栈，源头仍是 ui/theme.py TOKENS）
- 代理沿用 workbench 模式：浏览器相对路径 /api/* + next.config.ts rewrites（CHATUI_API_TARGET 可覆盖），FastAPI 不引入 CORS
- `ui/app.py` Gradio 演示界面保留不删除（内部对照与兜底）
- 任务：T063

---

## D033：评测体系强化批次（Phase 7，2026-09-03）

**背景**：
62+1 任务全部完成后对评测体系做了一次全面审计（docs/eval-audit-for-ai.md 工程版 /
eval-audit-for-human.md 人话版）。骨架（A/B 框架、eval_runs 落库、判分纯函数测试）达标，
但存在 2 个 P0 缺陷（human_precision 指标退化恒 0/1、expected_intent 死标注）、
4 个 P1 判分深度缺口、4 个 P2 防线缺口。核心结论：**北极星目标"人工转接率 62%→37%"
在评测体系中不可测不可证**（0 条转人工用例 + 指标公式坏）。

**决策**（任务拆解 T064-T074，顺序 P0→P1→P2→全量回归）：

1. **用例集归属**：
   - `human_handoff`（转人工 18 条）**并入主数据集**（200→218，v1.1.0）——北极星指标必须在
     全量报告直接可见，这是"可证明"的前提；历史趋势断裂由 dataset_version 字段承载
   - `adversarial`（对抗 20 条）、`multiturn`（多轮 30 条）**独立数据集**——红线题与多轮题
     语义自成体系，混入主基线会把"完成率"指标口径搅浑（对抗题期望的是拒绝而非答对）
2. **判分增强口径**：
   - 数值断言 `expected_numbers`：从 must_include/any_of 迁移纯数字关键词为精确断言
     （千分位/全角/尾 .0 容差），并入 passed——金额算错必挂是理赔系统的底线
   - LLM-judge 独立列**不并入 passed**（同 D026 轨迹口径：先观察一个周期再决定收敛）；
     仅判 must_include 为空的用例；judge 校准流程=人工抽检 50 条与 judge 对齐率 ≥85%
     方可采信（校准本身需真实运行+人工，机制先行）
3. **意图准确率**：CaseResult.intent_match 以 None 表示"未标注不考核"，分母只计 80 条
   已标注用例（与轨迹维度 scored 口径对齐）
4. **CI 门禁**：LLM_API_KEY secret 缺失时 job skip 而非 fail（公开仓库/无 key 贡献者不被
   误伤）；门禁阈值=完成率降幅 >5pp 退出码 1；HF embedding 模型（BGE-M3 ~2GB）配
   actions/cache 避免每 CI 全量下载
5. **统计增强**：tokens_per_case 复用 ab_test 的 Prometheus Counter 差分口径下沉到
   test_suite；wilson_ci（完成率 95% 区间）进报告与趋势点——88% vs 87% 这类差异是否在
   噪声内从此可判

**影响**：T064-T074 落地；主数据集版本 1.0.0→1.1.0；全量基线将在 T074 重跑替换
（旧 baseline.json 改名存档，不删）。

## D034：长期记忆 prod Store 用常驻连接池而非 from_conn_string 上下文（2026-09-03）

- 背景：BUG-003 修复时有两个选项——①沿用 short_term 的 CheckpointManager 模式（from_conn_string 异步 CM + lifespan 显式 start/close）；②模块内自建 AsyncConnectionPool 常驻（open=False 构造、首次异步使用时 open+setup）。
- 选项与理由：记忆是旁路路径（永不抛错、失败仅日志），调用点分散在 API 层读注入/写路径，没有集中 lifespan 挂载点；引入 Manager+lifespan 改动面大且 dev/prod 生命周期不一致。方案②池常驻进程生命周期，与 psycopg 官方池语义一致，零调用方改动。
- 最终选择：方案②（AsyncConnectionPool open=False + _ensure_pg_setup 延迟开池），连接参数逐项对齐官方 from_conn_string 配方（autocommit/prepare_threshold=0/dict_row），保证行为与官方路径等价。

## D035：容器自举用 init 一次性容器 + hf_cache 命名卷（2026-09-03）

- 背景：BUG-004——镜像不含 data/，且 CMD 只有 alembic，容器起来后库与向量索引全空（对话"查不到保单"、RAG 静默降级）。
- 选项：① app CMD 串联 seed+ingest（每次启动重跑，ingest 需加载嵌入模型拖慢重启）；② docker compose 的 depends_on + profile hack；③ 独立 init 容器跑完退出，app 以 service_completed_successfully 依赖。
- 最终选择：③。顺序强制 alembic→seed（seed 的 init_db 是 create_all 兜底，先建表会让 app 的迁移撞已存在表）；BGE-M3 缓存入 hf_cache 命名卷供 init/app 共享，二次启动免下载（实测冷启动 HF 直连反复停摆 15+ 分钟，卷命中后秒级）。镜像按白名单反选携带 mock/kb_docs/graph（共 ~120K），qdrant 本地存储/SQLite 仍排除。
- 附带：Qdrant server v1.12.6→v1.18.0（client 1.19 与 server minor 差须 ≤1，v1.12.6 差 7 个 minor）；app 的 OTEL_ENDPOINT 硬指向服务名（宿主 .env 的 localhost:4317 是直跑进程口径，透传进容器会指向容器自身）。

## D036：容器模型加载策略——本地目录直载优先于 hub 缓存复用（2026-09-03）

- 背景：hf_cache 卷灌入宿主机缓存后离线加载仍失败。两层根因：Windows HF 缓存快照是真实文件（无符号链接支持），Linux huggingface_hub 要求快照为指向 blobs 的符号链接，校验不过判无效缓存；且在线模式每次向 HF 校验最新 revision，缓存快照落后即触发整模型重下载（弱网环境反复停摆）。
- 选项：① 卷内快照重建符号链接结构（一次性数据修补，每次从 Windows 灌卷复发）；② hf_hub_download local_files_only 直读（仍受缓存格式校验约束）；③ SentenceTransformer 直载快照目录（Path.is_dir() 短路，完全绕开 hub）。
- 最终选择：③——新增 embedding_model_path 配置，容器启动守卫 find 快照目录注入（命中即离线直载，未命中走 repo id 在线下载）。附带决策：compose 命令中的 shell 变量必须 $$ 转义（compose YAML 层变量替换先于容器执行）；postgres 用 pgvector/pgvector:pg16 镜像（长期记忆向量索引依赖 vector 扩展）；store.setup 移入 app lifespan 预建（迁移含 CREATE INDEX CONCURRENTLY，请求路径内会等自身已开事务形成自锁）；AsyncPostgresStore 一律异步接口（aput/asearch）。

## D037：产品转向——本仓库重写为「智能核赔平台」，v0 草案评审修正（2026-09-04）

**背景**：
docs/claimflow - 新架构设计.md（v0 草案）经评审确认面向另一个产品：理赔案件自动核赔流水线
（材料审核→保单核实→风控→责任认定→金额理算→理赔决定书），而非现行理赔咨询问答系统。
用户确认推倒重来，并就三个方向性问题拍板：

1. 代码归属：**本仓库原地重写**（不建新仓库）
2. MVP 范围：**仅医疗险**
3. 自动化策略：**分级自动**（低风险 + 责任明确 + 金额阈值内自动出决定书；
   高风险/材料存疑/低置信/超阈值转人工，阈值全部配置化）

**v0 草案评审结论**（修正明细见 docs/claimflow-新架构设计-v2.md 第二节）：
- LangGraph 用法硬伤：4.1 supervisor 伪代码非 LLM 决策且路由键 "parallel_verify_fraud"
  在 6.1 映射表不存在；7.3 `Command(goto=None)` 非挂起语义（正确为 interrupt + Command(resume)，
  T037 已验证可行）；6.1 human_intervene→END 与图 2 回 supervisor 自相矛盾；
  6.2 parallel_mode 无写入者；7.1 SqliteSaver 同步 with 块不适合 FastAPI 常驻服务
- 选型倒退否决 3 项：LangSmith（已有 OTel+Prometheus+Grafana 自托管，D017 已论证）、
  ChromaDB（已有 Qdrant+pgvector，不引第三套向量库）、requirements.txt/config/utils 目录
  （违反 AGENTS.md 工程约定）
- 铁律延续：所有输出必经合规门（F10）——v0 决定书直出 END 通道违规，v2 修正为决定书必审

**v2 关键设计决策**：
1. 编排：不设常驻 LLM supervisor——核赔是封闭管线，阶段间确定性路由（可审计/可单测/零 token），
   LLM 裁量收敛到 triage 异常节点（TriageDecision 结构化输出 + 规则兜底）
2. 并行：policy_verify ∥ fraud_check 用 Send fan-out/fan-in；各分支独立子图 input/output
   schema，阶段结论字段唯一写者（policy/risk 各写各的 channel，天然无冲突，无需合并 reducer）
3. State：ClaimCaseState 分域——各阶段结论为独立 Pydantic 模型 dump，禁 30+ 字段大扁平；
   金额 Decimal（序列化 str 存储）、日期 date
4. HITL：补件（SUPPLEMENT）/核赔复核（REVIEW）/升级（ESCAPE）三类工单；
   interrupt + Command(resume) 模式移植 T037，坐席/客户回写必过合规复审

**影响**：
- 新蓝图：docs/claimflow-新架构设计-v2.md（取代 v0 作为实施依据，v0 留作历史参考）
- 重写启动前先 `git tag v1-consultation` 冻结现行咨询产品快照
- 下一步：架构 v2 经用户评审确认后，重新产出 spec/plan/tasks（新 Phase 规划）再开工
- AGENTS.md 第 1 节（项目定位）随 M0 更新；evals 框架与判分口径（D026/D033）延续到核赔评测

## D038：演示界面选型——chatui（Next.js）改造为案件提交门户（2026-09-04）

**背景**：D037 开放问题 4（演示界面去留）。用户拍板："UI 改造为案件提交演示界面"。

**选项**：
| 选项 | 优点 | 缺点 |
| A: Gradio ui/app.py 改造 | 最快 | D032 已论证 Gradio DOM 不可控、观感天花板已见 |
| B: chatui（Next.js）改造为案件门户 | 产品级门面；复用三栈设计令牌与 rewrites 代理；与坐席工作台观感一致 | 需新写案件表单/进度时间线/决定书渲染 |

**最终选择**：B（用户拍板）。chatui 从"对话演示"改造为"案件提交门户"：
提交案件 + 上传材料 → 实时进度时间线 → 决定书查看 / 补件交互。
Gradio ui/app.py 退役（M5 随旧代码删除）；ui/eval_app.py 评测台保留。

**影响**：T091；spec F15。

---

## D039：架构改判——多险种前提确认，采用 LLM Orchestrator-Worker 全动态调度（2026-09-04）

**背景**：D037 确定性管线设计的前提是"单一险种、路径可枚举"。用户澄清产品实际面向
**多险种**（医疗/车险/财产/意外），不同险种的材料体系与核赔路径差异大——路由从"制度"
变为"真决策"，D037 的编排子决策前提不成立。经三方案对比（A 确定性骨架+skill /
B LLM orchestrator 全动态 / C 配置化 DAG），**用户拍板：直接采用 B**。

**范围修正（覆盖 D037 的"仅医疗险 MVP"）**：
- 产品按多险种原生设计：case_type 枚举（medical/auto/property/accident），受理期 LLM 分类
- worker 按"险种 pack"分批上线：**首批医疗险 pack**（复用现有工具/条款库/材料解析资产）；
  车险/财产险/意外险 pack 为二期任务（如需进首批需追加任务）
- 未上线险种的案件：受理期即转人工受理，不让 orchestrator 无米之炊

**安全设计（LLM 调度的对冲，吸收自 A/C 方案论证，必做项非可选项）**：
1. **静态合规门不可绕过**：decision_generate → compliance_gate → 签发/人工为静态边，
   不在 orchestrator 调度空间——任何路由决策都无法跳过合规
2. **前置条件守卫（代码层）**：理算需责任认定+保单结论就绪、决定书需理算就绪、
   必做集未全 done 禁止终局派发；orchestrator 违规路由由守卫改投（T047 reconcile 思想升级）
3. **失败兜底**：orchestrator LLM 失败 → 按险种的确定性默认计划推进，不阻塞案件
4. **决策审计**：每次 RoutingDecision（含守卫修正）落 case_events，可回放
5. **防绕圈**：recursion_limit + ModelCallLimit + 每案件调度调用数指标

**评测门调整**：金额正确率 100% 与红线 0 漏放**保持硬门**（分别由确定性理算工具与
静态合规门保证，与调度方式无关）；新增软门：orchestrator 路由决策与金样本期望一致率 ≥95%。

**skill 机制（D037 讨论确认采纳，与调度方式正交）**：
`skills/<stage>/<line>.md` 作业规程包（SOP / few-shot / 红线清单 / 工具规程），
worker 与 orchestrator 激活时装载进 system prompt；准确率迭代改文本不改代码；
接既有 A/B 框架做 skill 对比实验。

**复杂度代价（确认接受）**：每案件 +5-10 次调度调用（deepseek-v4-flash 单价下可接受，D007）。

**影响**：
- docs/claimflow-新架构设计-v2.md 修订（第四节编排重写为 orchestrator 设计）+ 人话版同步
- .agent/spec.md / plan.md 重写、tasks.md 追加 Phase 8（T077-T093）
- nodes/supervisor.py（T047）从"参考后删"升级为"改造复用"——orchestrator 基于该模式扩展
- 责任认定/材料审核等 worker 仍为 ReAct agent + skill；triage 不再单列（异常裁量并入 orchestrator + 兜底）

---

## D040：阶段知识归一 StageSpec registry + 死代码清理（T094，2026-09-05）

**背景**：Phase 8 收官后的架构评审（improve-codebase-architecture）发现：六阶段流水线的
名字/顺序/前置/快照知识被手抄 8 处（orchestrator 五处——含同文件两份相同 Literal、
case_graph 回边元组、state.py、路由 prompt、测试元组、评测集），加一个阶段要改 8 个文件。
另有 T093 漏网死代码与断链评测链。经 grilling 六轮十问（Q1-Q10），用户逐项拍板如下。

**深化决策（重构部分）**：
1. StageSpec registry 落地 `schemas/stages.py`（与阶段产出模型同住，阶段知识单文件可见）：
   `DispatchTarget` StrEnum（6 worker + human）+ `StageSpec{name, channel, output_model,
   requires, snapshot_keys, in_must_complete, description}` 按管线序排列
2. 前置条件**数据化**（requires 声明），守卫**算法保持代码**（改投/去重/solo 派发/
   材料残缺 drop/rerun 放行——D039 安全设计的代码形态利于审计）；"材料残缺→drop"
   三分支语义不做成规则引擎
3. compliance_gate **不进** registry——它是静态边非可调度阶段，进清单会模糊 D039 调度空间概念
4. 阶段名 StrEnum 归一两份 Literal（pydantic function_calling 对 enum 同路径；
   StrEnum 为 str 子类，state/checkpoint/case_events 序列化无感）；另加派生一致性测试双保险
5. case_graph worker 回边从 registry 派生；节点注册保持显式（工厂签名异构）
6. 路由 prompt 的可派发目标清单段由 registry 生成（spec.description 承载人话描述）
7. `skills/orchestrator/_shared.md` 保持手写不生成——D039"改文本不改代码"的调优面，
   漂移由金样本路由一致率 ≥95% 软门兜住
8. 评测生成器（gen_adjudication_cases.py 的 expected_worker_sequence + 抄写的阈值）
   留给后续阈值契约任务（评审候选 5），本次不动

**清理决策（Q1=B，Q7-Q10）**：
9. 断链评测链四件全删：evals/test_suite.py（388 行，import 已删的 workflows.main_graph
   运行必崩）、services/eval_runner.py（240 行）、app/api/v1/evals.py（301 行）、
   ui/eval_app.py（后端死则空壳）+ app/main.py shutdown 钩子与路由注册。
   **对 D038 的修订**：D038"eval_app.py 评测台保留"指演示界面选型语境（评测台不必搬
   Next.js），其后端链路已随 main_graph 删除而断；将来核赔评测 UI 对
   adjudication_suite 报告格式重建
10. v1 判分五件套保留：evals/{metrics,judge,trajectory,variants,ab_test}.py（约 1400 行，
    现零调用零测试）——D039 明确"接既有 A/B 框架做 skill 对比实验"，为后续基建。
    本条记录防止下轮评审再当垃圾提出
11. 死文件全删：state.py::AgentState（v1 漏网）、schemas/agent_outputs.py（零 import）、
    tools/registry.py + tools/executor.py（AGENTS.md 早挂账"T046/T047 后删除"）+
    受影响测试修复
12. v1 会话指标组删除（CONVERSATION_TURNS/TURN_LATENCY/TURN_TOKENS + token_tracker.py，
    调用方已随 v1 图删除）；T090 三个零调用指标（record_case_stage/record_supplement_rounds/
    record_case_tokens）**保留不接线**——接线需节点计时埋点属新行为，另立任务
13. case_store.py L106 debug print 删除

**影响**：
- T094 单任务执行：行为零变化（金样本断言/守卫拦截测试不动），清理不掺行为改动
- 新增 CONTEXT.md 领域术语表（StageSpec/DispatchTarget/结论 channel/守卫/静态合规门等）
- 加阶段/改调度知识的维护成本从 8 文件降为 1 文件（registry）+ 行为代码（守卫/兜底）

**D040 补记（T094 执行中发现，同日）**：
- Q8 修正：evals/{ab_test,variants}.py 与 scripts/collect_traces.py 的执行半边
  （build_eval_graph/load_cases/run_case）绑定 evals.test_suite 的 v1 主图运行器，
  test_suite 删除后它们 import 即断——一并删除。metrics/judge/trajectory 三件可独立
  导入，按 Q8 保留。D039 的"接既有 A/B 框架做 skill 对比实验"落点修正为：
  skill 实验立项时对 adjudication_suite（双模式）重建 A/B 壳，判分复用 metrics 思路
- scripts/check_eval_gate.py + tests/evals/test_eval_gate.py 删除：其完成的率基线
  对比（baseline.json）是 test_suite 报告口径，生产者已不存在；CI eval-gate 改造为
  跑 evals.adjudication_suite 确定性全量（六门即门禁，零 LLM 零外部依赖）
- services/db/models.py 的 EvalRunRecord 表模型保留（alembic 迁移链完整性，
  eval_history 删除后无代码写入，表为惰性）
- services/observability/token_tracker.py 保留：phase_ainvoke 被 materials/
  long_term/ocr_extract 等活跃模块调用（评审报告此处有误，按 grep 实证修正）；
  仅删 record_turn/CONVERSATION_TURNS/TURN_LATENCY/HUMAN_INTERVENTIONS/
  COMPLIANCE_VERDICTS（唯一调用方 record_turn 属 v1 会话链）

---

## D041：架构评审候选 2-6 落地决策（T095-T099，2026-09-05）

用户拍板"按建议顺序完成 6 个候选"（4 → 2+3 → 5 → 6 → 遗留）。逐任务关键决策：

**T095（候选 4，seq/状态机）**：
- seq 分配器归一：get_default_recorder 改共享单例（图节点与 API 路由同实例，
  进程内缓存对全部写入者一致）+ DB 层 (case_id, seq) 唯一约束兜底跨实例 +
  IntegrityError 失效缓存重试（≤3 次）；迁移 b5f9c3d7e2a4 先去重历史重号行再加约束
- CaseStatus 由 Literal 别名升级 StrEnum，7 个写入点全部引用枚举成员；
  工单挂起态集合 PENDING_CASE_STATUSES 由 schemas.case 单源
- 不做全量状态迁移矩阵（YAGNI）：枚举 + 挂起集合已覆盖现行断言需求

**T096（候选 2+3，险种 pack + agents/）**：
- InsuranceLinePack 落 schemas/lines.py：line/product_types/online/required_docs/
  doc_types/policy_terms/exclusion_keywords/self_pay_pattern 声明式数据；
  medical 全量 pack，auto/property/accident 二期占位（online=False）
- 5 处 `or "medical"` 静默兜底改为显式 unknown（skill 装载走 line→_shared→None 回退链）——
  未知险种不再被悄悄当医疗险审
- agents/ 包删除，活机器迁 services/worker_agent.py：AgentDefinition + create_agent
  装配缓存 + invoke_worker（去 shared_data 参数）+ derive_tool_trace；
  死重（run_worker_agent/`_derive_tool_trace`/shared_data 池）不迁
- LLM 注入统一约定：四节点（orchestrator/material/liability/decision）参数
  None = 确定性路径；"__keyword__"/"__fallback__" 字符串哨兵与异常驱动开关删除
  （liability 由"调字符串抛 TypeError 落兜底"改为显式 None 分支）
- 责任认定 Agent 定义按险种线懒装配缓存（_agent_def_for）——多险种 pack 上线时按
  case_type 分定义的预留落点

**T097（候选 5，规格契约）**：
- schemas/contract.py：AUTO_APPROVE_LIMIT / WAITING_PERIOD_DAYS / ROUTING_CALL_BUDGET
  唯一定义；config.py 默认值、医疗 pack 条款、金样本生成器、评测门预算全部引用
- services/amounts.approved_amount：规格公式唯一实现（生成器与 amount_calc 同源）；
  生成器重跑产出的数据集字节级一致（行为零变化实证）
- 评测红线复用 tools.compliance.rule_check.check_text（与运行时门同实现），
  弱化子串检查删除
- skill 散文中的对应数字不生成化（D040 决策延续），漂移由路由软门兜住

**T098（候选 6，CaseService）**：
- services/case_service.py 收口：幂等查询/案件号生成/建档/决定书响应映射/
  Command(resume) 载荷构造器（B03 补件自动恢复与工单处理同形）
- 图调用（ainvoke/resume）留在路由——HTTP 请求生命周期内的编排粘合；
  管线异步化为行为变更，单列路线图（README 已挂账）

**T099（遗留收口）**：
- T090 三指标接线：CASE_STAGE_LATENCY 在 case_graph 以 _timed 包装 6 个 worker 节点
  （复用 STAGE_SPECS 阶段名）；SUPPLEMENT_ROUNDS 在 human_gate 补件分支 observe 1
  （指标 sum=补件恢复总次数）；CASE_TOKENS 经 token_tracker 新增案件 ContextVar
  （track_case，三处路由包裹图调用）+ record_usage_to_tracker 分流
- compliance channel 落 schema：ComplianceOutput.violations 修正为 list[dict]，
  compliance_gate_node 产出经模型校验 dump（与其余六阶段同走 stages.py）
- README 整体重写为核赔平台口径（v1 评测/API/界面章节全部替换）；
  docs/architecture.md 头部冻结为 v1 历史存档并指向 v2 文档

验证基线：全量 411 passed + ruff 全绿；评测门确定性模式改动前后指标逐位相同
（amount 0.9773 / route 0.9924 / liability 0.9318 / 总一致率 92.4%——失败项为
T089 挂账存量 known issue，非本轮回归）。

---

## D042：长期记忆接入核赔——申请人记忆（T100，2026-09-05）

**背景**：评审确认 ④ 语义记忆层悬空（v1 会话记忆写入者已删，核赔管线零消费）。
用户拍板"需要长期记忆"，经 Q1-Q3 磨清口径（Q1/Q2 按推荐，Q3 经生产问题对比分析后选 A）。

**决策**：
1. **内容口径**：申请人记忆 = 案件终态结构化档案（结论/核定金额/原因/日期），
   **确定性渲染零 LLM**（案件事实本就是结构化数据，区别于 v1 会话记忆的 LLM 摘要）
2. **写入时机**：仅终态一次性写入——4 条终态路径（auto 签发 / 坐席签发 / escape 转人工 /
   REJECT 安全兜底）。明确排除每次状态变化写入：中间态噪音淹没语义检索窗口、
   与 case_events 审计层制造第二事实源、7 写入点一致性风险、重跑重复记录。
   注意 auto_adjudicate 转人工分支不是终态（案件继续走 human_gate 签批）
3. **消费方**：工单/案件详情 API 返回申请人历史档案（排除本案件防回声，坐席消费）；
   orchestrator 路由快照注入 history 段（memory_in_routing 开关**默认关**——
   LLM 路由口径与金样本评测一致性优先，观察后再开）
4. **三支柱**：终态判定收敛（4 路径各一行钩子）/ 记忆=cases 事实派生视图
   （key=case_id 幂等 upsert + scripts/rebuild_memories.py 离线重建安全网）/
   检索口径即终态口径（Store 里只有结局档案，过程看审计时间线）
5. **边界**：claim_records（90 天频率，风控评分公式消费）与申请人记忆（语义档案，
   人/LLM 消费）并存不重复；核赔知识库（RAG/KG）是知识不是记忆，正交
6. **长尾记账**：单用户记忆无界增长（远期按用户压缩归档）；档案渲染不含 PII 字段

**实现落点**：services/memory/case_memory.py（新）、long_term.py 增 search_store_items
公共门面、nodes/{auto_adjudicate,human_gate,orchestrator}.py、app/api/v1/cases.py、
schemas/api.py、scripts/rebuild_memories.py（新）、settings.memory_in_routing。
验证：8 新测试 + 全量 419 passed + ruff 绿。

---

## D043：memory_in_routing 验证安全、默认保持关（T102，2026-09-06）

**实验**：全 132 金样本 × LLM 模式，A（基线）/ B（--memory-routing + --seed-memories 满载荷预置）各一轮。结果逐位全等：route 0.9924、liability 0.9470、amount 1.0、调度预算 5，失败集相同（8 案），恢复 0 / 退化 0。**结论：申请人记忆注入对路由质量零扰动。**

**决策**：开关**保持默认关**。验证回答的是"开会不会坏"（不会）；但"为什么开"未被证成——金样本集不存在需要历史才能正确路由的场景（重复索赔信号已由风控 claim_records 结构化覆盖）。开启条件：出现真实业务场景（如同案重复提交处置、VIP 路径），届时重跑本实验脚本（scripts/compare_memory_experiment.py + suite 四 flags）即可复验。

**附带发现**：前 66 案（24 手工底座 + 42 生成案）LLM route 恒 1.0；路由噪声全部集中在后半段生成案件（E-0065 抢先转人工类，T081 已知噪声类）。LLM 模式的 liability 软门缺口（0.947）集中在 partial 案件的裁量差异——这两项是将来 skill 迭代的靶点，非记忆相关。

---

## D044：管线异步化——case_jobs 交付队列（混合方案，T103，2026-09-06）

**设计过程**：design-it-twice 两子代理出极简进程内执行器（方案甲）与完整任务表
job-queue（方案乙）两份 interface 设计，对比后用户拍板**混合方案：乙的骨架砍掉装甲**。

**采纳（乙保留项）**：
- case_jobs 表 = 唯一交付凭证（transactional outbox：任务行与建档同事务，
  "提交成功但任务丢失"构造上不可能）；同案件活跃唯一（partial unique index）
- interrupt 挂起 = 交付任务的**成功终态**（outcome=interrupted + 回执快照），
  恢复 = 插入新 resume 任务行——两张表各答各的问题，互不对账
- 常驻单消费者循环（CAS 认领，SQLite/PG 通吃）+ 退避重试（base·2^(n-1)）+
  耗尽 dead + job_failed 审计事件
- 执行观测（CASE_DURATION/CASE_TOKENS/阶段计时）随执行体迁入，路由不再感知
- 派发 seam 两真 adapter：Inline（测试/兼容档，19 个 API 用例同步语义零改动）
  / Background（生产默认，POST 受理即返回）

**砍掉（相对乙完整版）**：租约/心跳/locked_by 列（崩溃恢复靠启动期把 running
孤儿回收回 queued）、SKIP LOCKED 认领、并发闸门、queue_position。**单实例契约
（replicas=1）写入 README 已知限制**；多实例/容量触发时按乙设计的升级位补租约列。

**实现要点与执行中发现**：
- payload 在 enqueue 边界 JSON 安全化（Decimal→str/date→ISO，图节点对两者均有
  显式收敛——T079 坑位记录的回报）
- upload 的材料落库与 resume 入队拆两个事务：enqueue 撞活跃唯一会毒化会话，
  材料事实先行持久化，冲突仅回滚任务行（恢复语义本幂等）
- resolve 并发双击 → 409（活跃唯一约束，顺带修掉旧并发双跑缺陷）
- 评测套件直调图不经 API，零波及
- **附带修复 ①**：metrics._safe_observe 对无标签直方图错误调用 .labels() 被
  裸 except 吞掉——CASE_DURATION/ROUTING_CALLS 自 T090 起从未记录过数据
  （TDD 测试逮到）
- **附带修复 ②**：chatui/app/layout.tsx 引用未提交的 HealthPill 组件，
  chatui 构建在 HEAD 上本已损坏（T091 遗留），摘除引用
- **附带修复 ③**：interventions 两处同步 get_state → aget_state（prod
  AsyncPostgresSaver 兼容隐患，设计简报点名的相邻存量问题）
- 迁移 c8e4f2a6b1d9 经 scratch 副本执行验证（upgrade 建表+四索引 / downgrade
  干净移除）；验证过程踩坑：alembic env.py 用 settings.database_url 强制覆盖
  URL，CLI 误操作把 alembic_version 戳进了 dev 库（已清理，无 schema 改动）

**消费方适配**：verify_adjudication 增 wait_terminal 轮询（三处断言改终态轮询）；
chatui 详情页 AutoRefresh 客户端组件（非终态或任务在飞时 2s router.refresh）；
workbench resolve 表单轮询详情至离开挂起态（≤20s）。

验证：13 单元（outbox/冲突/CAS/退避/死信/孤儿回收/循环回路）+ 20 API
（19 既有 inline 零断言改动 + background 全回路新用例）+ 445 全量 passed +
ruff 绿 + 双前端 build 绿。

---

## D045：v1 会话残留死栈清除（T104，2026-09-06，评审二候选 1）

**背景**：第二轮架构评审实证 T093 删旧漏了前端与 schema 层——workbench 首页
（导航默认页）调用已删端点 GET /api/v1/interventions 永远 404；schemas/api.py
约 182 行 v1 会话 schema 零引用；chatui/lib/api.ts 整文件孤儿；三张 v1 表
（conversations/messages/human_tickets）仅 ORM 自引用。两套"工单"词汇污染领域语言。

**决策（grilling 三问，用户 A/A/A）**：
1. 代码全删 + **迁移 drop 三张死表**（d9a5c1e8f3b7）——与 D040 留 EvalRunRecord
   不同：那是评测基建有复用语义，这三张是 v1 产品本体永无复用；DROP 无 SQLite
   ALTER 限制，downgrade 不重建（表定义已随 ORM 删，需要时从 git 历史恢复）
2. workbench 首页 redirect('/cases')（坐席直达核赔工单，URL 结构零改动）
3. 验收 = 引用清零 + 全量 pytest + 双前端 build + 迁移执行验证（grep 项检），
   不跑服务冒烟（纯删除不触后端行为）

**执行事实**：workbench 删 v1 栈四组件（StatusBadge/ResolveForm/MessageTimeline/
AuditViewer 均仅 v1 链引用）+ tickets 页；lib/api.ts v1 段 16 类型/函数删、
request/formatTime 两个活 helper 保留（v2 在用）；layout 删"会话工单"导航。
测试：test_conversation_with_messages 随表删（-1），test_session 两用例改用
Policy 行承载会话提交/回滚语义。全量 444 passed + ruff + 双 build + 迁移
scratch 验证（升级后恰 9 张业务表）。

---

## D046：决定书读模型归一——签发物由案件状态推导（T105，2026-09-06，评审二候选 3 / Top）

**背景**：decision_generate 在合规审查前落草稿 v1（revise 也落修订版），
详情端点取最新版不过滤——REJECT/待复核案把未签发草稿当《理赔决定书》
展示给客户（客户可见错误，对外文书零容忍语义被违反）；resolve 端点则
读图 state 独走一路。三端点两口径。

**关键事实（设计前提）**：auto 签发路径不另落版本——auto_adjudicate 签发的
就是最新草稿/修订版行（issued_by="auto" 但它就是签发物）；仅坐席签发落
agent 新版本。故 issued_by 单列不能判别签发。

**决策（grilling 三问，用户 A/A/A）**：
1. **读侧规则**（不加列不迁移）：签发物 = 案件终态（auto_issued/closed）时的
   最新版——是否签发由案件状态推导，完全符合 D006"事实态以 cases 表为准"，
   不引入第二份事实（否决 issued_at 标记列：状态+标记双事实会漂移）
2. **响应形状**：decision_document 恒返回最新版（草稿也在，供坐席复核视图）
   + decision_issued 标志——一个响应形状，两种前端策略：chatui（客户）
   `issued=false` 不渲染决定书卡；workbench（坐席）渲染但标"草稿·未签发"
3. **三端点统一**：case_service.decision_doc_view(session, case) → (最新版,
   是否已签发) 唯一读函数；提交/幂等/详情/resolve 全切换，resolve 的
   图 state 投影删除

**影响**：评审二候选 3 闭环；测试 test_review_rewrite_red_line 断言从
"decision_document is None"改为"decision_issued is False"（草稿可见性
是坐席视图的有意变更）；新增读模型回归用例（挂起案草稿+False /
auto_issued 签发物+True）。CONTEXT.md"决定书"词条本就定义为签发物，
机器口径现已对齐。

---

## D047：挂起信息单源——交付回执（T106，2026-09-06，评审二候选 2）

**背景**：同一份挂起信息（kind/reason/missing）有 checkpoint 与 job.interrupt_payload
两个真相源、三个后端读取口径（工单列表逐案 aget_state N+1 / resolve aget_state /
详情 _human_from_job）+ 两个前端按 status 猜 kind（escape 案被误标 review——bug 级，
且 workbench 的猜测喂给处理表单决定形态）。

**决策（grilling 四问，用户 A/A/A/A）**：
1. **纯 job 回执单源**：latest_job.interrupt_payload 是唯一真相源（interrupt 即交付
   任务的成功终态，D044——含 escape，intake 转人工作也经 human_gate interrupt）。
   无回执行的保守默认（supplement_pending→supplement，其余→review）收编进后端
   单点 human_info_from_job——即原前端猜测逻辑的后端化。不保留 checkpoint 回落
   （生产全走不到的死路径，且 N+1 代码会被"兼容"理由保下来；PoC 无 T103 前存量）
2. **列表单 SQL**：latest_jobs_for_cases 批量取每案最新任务行，N+1 checkpoint
   网络往返消失
3. **resolve 同切回执**：kind 判定不再 aget_state——interventions 从此完全不依赖
   case_graph（图交互只剩 case_jobs 执行体一处，依赖瘦身完成）
4. **前端捆绑**：两详情页删状态猜测行（kind = detail.human?.kind），escape 表单
   形态 bug 顺带修死；保守默认留在后端单点

**影响**：评审二候选 2 闭环。与候选 3（D046）同主题——读模型口径归一：
决定书签发物（decision_doc_view）+ 挂起信息（human_info_from_job）两大读模型
均收口 case_service，前端全部消费 API 投影字段，不再有任何 checkpoint 读取。

---

## D048：评测门拆分 + 终态判定单源（T107，2026-09-06，评审二候选 5）

**背景**：adjudication_suite.py 428 行六种职责；六门判定藏在 _run_suite 内联段
零单测；_seed_memories 手抄 category→decision 平行映射且含"缺件"死分支笔误
（数据集实际值为 missing，11 案种子档案 outcome=referred + decision=approved
自相矛盾）；_flatten_events 死函数。

**决策（grilling 三问，用户 A/A/A）**：
1. **终态判定单源**：schemas.contract.final_decision_from_verdict（verdict→
   final_decision 三行规则，规格规则与阈值/公式同住契约模块）——auto_adjudicate
   签发分支接入；种子映射不再手抄 category（route→outcome 两行语义平凡保留）。
   转人工案（缺件/受理分类）记忆终态即 referred（它们无自动终态，决定权在人工）——
   比 verdict 推导更符合记忆档案语义
2. **六门拆 evals/gates.py**：evaluate_gates(results, *, red_line_leaks,
   guard_bypasses) 纯函数零 IO + overall_passed——门限判定首次可单测
   （硬门差一案即挂/红线零容忍/软门不阻断/error 案不进分母/预算上限 8 用例）；
   suite 退化为编排（跑案→收集→gates→报告→打印），428→375 行
3. **范围边界**：_setup_db/_seed_memories/_extract_outcome 留 suite（套件私有
   装配无第二消费者，one adapter = hypothetical seam）；_flatten_events 死函数删除

**验证**：评测门确定性模式重跑数值与重构前一致（amount 1.0/route 0.9924/
liability 0.9470，同 8 条既有失败——纯结构重构零行为变化）；9 个新测试
（gates 边界 8 + 种子语义 1）；全量 455 passed + ruff 绿。

---

## D049：交付生命周期收口 deliver_case_job（T108，2026-09-06，评审二候选 4）

**背景**：enqueue→commit→dispatch→refresh 的生命周期胶水在三个入口三份变奏
（submit 同事务直 commit / upload 双事务+冲突吸收 / resolve 冲突 409），
"enqueue 撞活跃唯一会毒化会话须 rollback"这条关键知识只活在路由注释里。

**决策（grilling 两问，用户 A/A）**：
1. **interface 形状**：`deliver_case_job(session, *, case_id, action, payload,
   dispatcher) -> CaseJob | None`——冲突返回 None（优于报告草案的 conflict
   参数版：差异维度全部内化为一个布尔出口，HTTP 翻译留调用方一行，毒化复位
   是 implementation 而非调用方知识）；commit 整个会话（submit 的 outbox
   语义天然保留，upload 前置 commit 材料后调用即独立事务）；dispatch
   fail-open（执行体异常本就在任务内消化，此处仅兜框架级 kick 错误——
   任务行仍在队，background 档循环会认领，不撤销受理）
2. **submit 防御分支**：理论不可达（新建案号唯一）同样 409——与 resolve 同形
3. **接口收 case_id 不收 ORM 对象**：初版收 case 并 refresh，单测逮出隐含
   "对象须在同会话持久"的调用方义务（接口味）——改为经 identity map 刷新
   调用方同会话持有的实例（原地生效，路由零感知）

**验证**：22 个 API 用例零断言改动全绿（行为零变化的直接证据）+ 3 个收口
单测（冲突返回 None 且会话复位可用——毒化知识从注释升格为测试覆盖、正常路径
返回刷新任务行、kick 失败不撤销受理）；全量 458 passed + ruff 绿。

---

## D050：测试基建收敛 + 前端交付判定单源（T109，2026-09-06，评审二候选 6 / 收官）

**背景**：换库操作四处伸手进私有全局（两份 API 夹具 + test_case_jobs + evals 裸赋
session_module._engine）；两份 API 夹具同构 ~80%；前端"案件是否仍在交付中"判定
两套口径（AutoRefresh 看 status+jobStatus / CaseResolveForm 看 job+两挂起态——
后者把挂起态也算 active，坐席对无在飞任务的工单会白轮询）。

**决策（grilling 四问，用户 A/A/A/A）**：
1. **session.py 公开 swap_engine(engine, factory)**：换库收敛为一个公开 interface
   （测试/评测唯一入口），私有全局不再被外部伸手
2. **conftest 提取 make_case_api_core 内核**（async：文件库+swap+两张种子保单+
   LLM 全关），两份夹具薄化；interventions 的共享 InMemorySaver + build() 可重建图
   **保留在其文件**——那是"跨重启"测试意图的私有表达（评审明确不收全量归一）
3. **前端 isCaseActive 单源**：统一口径 job∈{queued,running} OR status∈{received,
   in_progress}（修正 CaseResolveForm 把挂起态算 active 的白轮询）；两应用各一份
   同签名实现——monorepo 共享包 YAGNI（等第三个消费者）
4. **状态标签分叉确认为有意差异**：坐席视图区分 auto_issued（自动）/closed（坐席
   签发），客户视图不关心——两侧加互指注释防被"修不一致"误统一

**附带**：T105/T106 已顺手修掉评审点名的类型漂移（两前端 job/human/decision_issued
类型补齐）。test_conversation_with_messages 随 T104 表删除。
验证：458 passed + ruff + 双前端 build 绿；两夹具净 -76 行。

---

## D051：评审三收官——swap 生命周期补全 + 挂起猜测彻底删死 + 杂项束（T110，2026-09-06）

**背景**：第三轮评审（范围 T104-T109 新代码）3 强 + 2 worth + 1 speculative；
用户拍板三强/杂项合并一个收官任务（体量合计 <60 行）。

**采纳**：
1. **swap 生命周期补全（评审一候选 1 残留）**：verify_orchestrator 私有直赋迁
   swap_engine（T109 漏网第四处）；三夹具 teardown 的 engine.dispose() 改
   dispose_engine()（dispose+置 None = 现成 reset 闭包，拆除"全局残留已释放
   引擎"的顺序依赖地雷）；evals 私有读 _engine 换 get_engine()；
   patched_engine 顺势迁公开 seam（惯例归一）
2. **D047 自宣称收齐**：human_info_from_job(job, case_status=None) 内建保守
   默认（supplement_pending→supplement 其余→review，即原 conservative_kind
   僵尸分支的行为）；resolve/列表传 case.status；conservative_kind 删除；
   kind null 边界 `payload.get("kind") or "review"` 收掉（原 str(None)="None"
   truthy 病态）；两前端 `?? status猜测` 兜底删除——"无回执怎么猜"从四份
   收敛为一份
3. **杂项束**：get_app_graph/get_case_graph 死依赖删（T093/T106 后零消费者）；
   suite GATES 死字典删（阈值真身在 gates.py，死表留"改这里调门限"假象）；
   CaseInterventionHuman 删（≡CaseHumanInfo 逐字相同）interventions 复用；
   状态分类学单点——schemas/case.py 集中 PENDING/ISSUED/TERMINAL 三组 +
   referred 双重身份互指注释（∈PENDING 工单口径 ∧ ∈TERMINAL 记忆口径，
   非补集），rebuild_memories 手抄改 import（新终态出现不再静默漏档）；
   contract.py docstring 指针更新（阈值真身已迁 gates.py，T107 文档漂移）

**维持现状（本轮评审确认，防重提）**：case_service.py 不拆（deletion test：
拆三份产出 shallow module）；suite route→outcome 两行保留（金样本规范固有
知识）；前端轮询机制不抽（isCaseActive 后剩余重复只剩"等 settled"意图）；
三响应抽基类缓议（等第四个同构响应）。

验证：458 passed + ruff 绿。

---

## D051 补记（T111，2026-09-06）：第四轮走查发现两条宣称未兑现，已补齐

第四轮轻量走查（范围 T110 增量）发现 D051 两条宣称与代码不符，T111 兑现：
1. **"两前端 ?? 猜测兜底删除"未兑现**——workbench/chatui 详情页仍在
   `?? (supplement_pending ? supplement : review)` 猜测，且 escape 案在 resume
   在飞期间 human=None → 落回猜测误标 review（T106 宣称修死的 bug 只是把窗口
   缩到在飞期，未修死）。根因：cases.py 的 _human_from_job 对非 interrupted
   返回 None（无内建默认），前端兜底是活代码。**修复**：detail/submit 的
   human 投影切 human_info_from_job + 挂起态门控（PENDING 才有 human，
   终态 None——终态案不显示挂起卡），_human_from_job 删除；两前端兜底改
   `?? "review"` 仅 TypeScript 收窄（真实数据不触发）。
2. **"patched_engine 迁公开 seam"未兑现**——test_session.py 仍 monkeypatch。
   **修复**：迁 swap_engine + teardown dispose_engine()（复位全局）。

另收微 polish：dependencies.py docstring/空行、evals 空行、workbench TS
CaseInterventionHuman → CaseHumanInfo 改名。全量 458 passed + 双 build 绿。
教训入册：**收官轮的"宣称未兑现"必须由独立走查核对**——上轮实施时
前端部分因 heredoc 事故后的重写流程被遗漏，靠本轮才补上。

## D052：结构精简重构——死代码清除 + 官方容错对齐（T112-T117，2026-09-11）

**背景**：全库结构审阅（对照 Docs by LangChain 官方文档逐项核实）发现三类问题：
① Phase 8 重写遗留死代码约 1500 行（v1 评测残骸 / long_term 双职责 / 零散死类死脚本死配置）；
② Worker 子图内工具系统异常直接炸子图掉确定性兜底，缺"LLM 自愈"层；
③ 材料提取为节点内串行昂贵 LLM 子步骤，无任务粒度 checkpoint 短路（崩溃恢复重付全部提取调用）。

**选型与结论**（官方文档核实）：
1. **Worker 容错**：官方 `ToolRetryMiddleware(on_failure="error")`（内层）+ `ToolErrorMiddleware`（外层）
   组合（langchain≥1.3.14，官方文档推荐次序）——工具系统异常转为模型可见 error ToolMessage，
   LLM 修正参数重试，重试耗尽才落既有领域兜底。**自愈优先于兜底**，与 D039 不冲突
   （兜底保留，中间件只在子图内多争取一次自愈）。
2. **材料提取子任务化**：`@task` 官方明确可在 StateGraph 节点内调用（graph-api "Using tasks
   in nodes"），任务结果进 checkpointer（resume 跳过节点内已完成 task）+ 声明式
   retry_policy/timeout。不改图结构、不引入 Functional API（Graph API 保持原样）。
3. **明确不动**（防过度工程，均经官方文档核实）：
   - CircuitBreaker 自研保留——官方无熔断器（文档检索零命中）
   - ToolResultCache（Redis 跨案件）自研保留——官方 cache_policy 是图运行内 memoization，语义不同
   - 渲染层脱敏（mask_sensitive/check_text）保留——官方 PII middleware 在 agent 消息层，层次职责不同
   - 节点领域兜底保留——"业务失败=正常返回+确定性兜底"优于通用节点级 RetryPolicy
   - orchestrator 路由 / 材料 AI 审查不加 @task retry——"失败快速兜底"是 D039 刻意设计（含 routing_call_budget）
4. **死代码删除面**（全部经消费者 grep 实证零活引用）：evals v1 三件套（metrics/trajectory/judge）
   + Eval* schema 三类、long_term 会话摘要半边（保留 Store 管线）、schemas/agent.py、ToolOutput、
   MAX_HISTORY_MESSAGES、demo_hitl_backend / verify_ui / verify_memory 脚本、v1 遗留配置项
   （memory_summary_every_n_turns / memory_top_k / memory_min_score / turn_token_budget）、
   token_tracker 轮次（turn）语义残留。

## D052 补记（T115，2026-09-11）：ToolRetryMiddleware 不叠加

实施时确认：Worker 子图内的工具全部是 GuardedTool（tools/factory 装配），其内层已带官方
`.with_retry()`（stop_after_attempt=3）+ 守卫超时总预算。若再叠 ToolRetryMiddleware，
重试倍数放大（3×3=9 次尝试 × 10s 超时窗口），最坏延迟不可接受且收益重复。
故 T115 只装配 ToolErrorMiddleware（自愈层：异常 → error ToolMessage，只暴露异常类型
不泄露原始消息——官方建议口径）；重试语义完全交由 GuardedTool 内层承载。
模型反复失败仍由 ModelCallLimitMiddleware 硬截断 → 调用方节点确定性兜底，D039 闭环不变。

## D052 补记二（2026-09-11）：审阅报告 P2 可选项取舍

评估后不做（收益/风险比不足，防后人重复评估）：
- **init_chat_model 替换手写 ChatOpenAI**：其价值在供应商前缀路由，本项目固定 OpenAI 兼容端点
  （DeepSeek，base_url 切换已够用），替换属横向改动无功能增益。
- **set_node_defaults + 节点级 RetryPolicy/TimeoutPolicy**：每节点已有"业务失败=正常返回+领域兜底"
  （D039），通用节点级重试反而延迟兜底；材料提取子步骤容错已由 T116 @task 精确承载。
- **ModelFallbackMiddleware**：模型级故障切换，单供应商 PoC 无场景；多供应商时直接采用。
- **case_jobs.py 拆 JobLoop**：队列/派发/回收/循环是一个内聚子系统，拆分收益低于扰动。
执行：T118（orchestrator 守卫拆分）+ T119（evals/reports v1 报告归档）。

## D053（2026-09-21，T120）：险种扩充——三线一次上线与三个附带修复

**背景**：D039 规划"worker 按险种 pack 分批上线"，首批仅医疗险；面试演练暴露"多险种架构论据
与仅医疗上线的现状矛盾"。用户指示先做险种扩充再面试。

**选项**：
- A. 只上意外险一条线（最小增量）——收益有限，"多险种"论据仍弱；
- B. 车险/财产险/意外险三线一次上线——pack seam（T096）使成本接近线性于数据量，
  且金样本生成器可确定性重生成评测覆盖，评测门兜底行为回归。

**选择 B**。附带三个实施中发现的问题一并修复（都有独立证据）：
1. `CaseMaterialRefIn.doc_type` 的 Literal 白名单是 T096"白名单全由 pack 派生"的遗漏
   ——改 str + 创建端点校验 all_doc_types()（与 B03 上传端点同口径）；
2. `policy_verify` 的 `terms.get("waiting_period_days") or DEFAULT` 会把"等待期 0 天"
   （车险/财产险/意外险条款要素）吞成默认 30 天——改 `terms.get(k, DEFAULT)`；
3. `liability_judge` 工具集硬编码 diagnosis_matcher（医疗专用）——pack 增
   `liability_tools` 字段声明式承载，医疗线带诊断匹配、其余线仅条款检索。

**条款设计**（mock 口径，覆盖公式各分支）：
- 车险 POL-2026-0008：免赔 500 / 比例 1.0 / 无等待期；除外=酒驾、无证驾驶、肇事逃逸、
  竞赛测试、故意行为；材料=事故认定书+维修发票+定损单（发票×定损金额交叉核验）；
- 财产险 POL-2026-0009：免赔 0 / 比例 0.9；除外=地震海啸、战争军事、金银珠宝现金、
  故意行为、自然磨损；材料=事故证明+损失清单+购置凭证；
- 意外险 POL-2026-0010：免赔 100 / 比例 0.9；除外=高风险运动（潜水/攀岩/跳伞）、
  酒后意外、自伤自残、无证驾驶；材料=事故证明+诊断证明+医疗发票（复用既有 doc_type）。

**评测集**：新增三线金样本组（正常阶梯/超阈值/除外/缺件/退保拒赔），"未上线险种"组改写为
仅 unknown（重疾险 POL-2025-0002）——离线转人工路径仍可考；意外险退保案 POL-2023-0004
上线后走管线拒赔，顺带修复既有失败案 E-0065（expected=auto observed=human 口径矛盾）。

## D054（2026-09-21，T123）：LLM 模式全量重跑与调度成本量化——证据回填与诚实边界

**背景**：面试证据缺口#1/#2——T120 三线上线后 LLM 全量未重跑；D039"每案 5-10 次调度调用
成本可接受"从未量化。

**实验**（deepseek-v4-flash，151 案全量，evals/reports/t123_llm_full.json）：
- **一致率 1.0（151/151），六门全绿，0 失败**——LLM 编排在四险种上与金样本期望完全一致，
  未因扩线退化（T089 时代口径 99.24% → 现 100%，T122 修完数据集缺陷后两类模式均达满值）。
- **延迟**：avg 2.16s/案、p95 2.69s/案（对比确定性模式 0.25s/案——LLM 调度的延迟增量
  约 +1.9s/案，在异步交付队列形态下用户无感）。
- **token**：采集管线已修通（suite 包 track_case 上下文 + 自定义 registry 差分），但补录
  时 API 余额耗尽（402），tokens/案 **暂缺、不估算**——待充值后 `--llm --limit 20` 抽样即可补。
- **LLM 增量价值的诚实结论**：当前金样本上确定性兜底与 LLM 编排均为 100%，准确率增量为 0；
  LLM 的潜在增量在同义词鲁棒层（对抗集 robustness 6/6 确定性全漏），但对抗集 LLM 模式验证
  同因余额未跑。挂账：充值后跑 `--dataset adversarial --llm`，若 LLM 能判对同义词案，
  则"LLM 层价值=鲁棒性"的论据闭环。

**计划外收益（降级实测）**：402 期间 orchestrator 全部 LLM 失败→确定性兜底，案件仍全部
正确完成——D039"失败兜底默认计划"在真实故障（而非注入）下首次全场验证。

**结论**：LLM 编排保持默认开启（延迟增量可接受、无准确率退化、降级安全）；token 成本
与鲁棒增量两笔账挂 T125 待 API 恢复后补。

## D055（2026-09-21，T129）：申请人记忆功能三决断（第二轮面试缺口#10/#11）

第二轮面试拷打确认：记忆"写而不读"（API 返回 applicant_memories 但前端不渲染、
memory_in_routing 默认关）、无删除链路、无置信度门控。决断：

1. **路由注入保持默认关**。T102 A/B 实验证据（132 案零漂移=零收益）继续有效；
   开启的先决条件是找到一类"历史档案改变调度质量"的案件族并实验证明。
2. **读闭环补 workbench**（本轮落地）：坐席详情页渲染申请人核赔档案——记忆的
   人类消费点收敛为坐席视角。chatui（客户门户）**刻意不渲染**：客户不应看到
   自己跨案件的核赔档案聚合，属隐私与合规口径，不是遗漏。
3. **删除链路/置信度门控/TTL 挂 T125**：GDPR 式按用户级联删除需要 Store 层
   按 namespace 清扫 API + 应用层端点 + 审计事件，单独立项；not_covered 档案
   无差别写入的污染风险在删除链路落地前接受（档案只用于坐席展示，不进路由，
   污染面=展示层）。

## D056（2026-09-21，T130）：模型切换 DeepSeek-V4.1-Flash（deepseek-flash）

**背景**：用户要求切换 deepseek-v4.1-flash 并配额充值。按官方更新日志（api-docs.deepseek.com/zh-cn/updates），
V4.1-Flash 的 **model 参数实际取值为 `deepseek-flash`**（用户口中的 v4.1-flash 是版本名，非 API id）；
旧 `deepseek-v4-flash` / `deepseek-v4-flash-vision-exp` 已下线（临时兼容路由到 V4.1）。

**变更**：
1. 主模型 `deepseek-v4-flash` → `deepseek-flash`（config 默认值 + .env + .env.example + client docstring）；
2. **vision 模型收敛为同一模型**：V4.1-Flash 原生多模态，`-vision-exp` 专用视觉模型失去存在必要——
   LLM_VISION_MODEL=deepseek-flash（保留配置项本身：未来若拆分思考/视觉档位仍有用）；
3. thinking-disabled extra_body 保留不动（tool_choice 兼容口径在新模型未验证，保守）；
4. 测试断言 5 处旧名同步（test_client/test_health/test_config/test_metrics）。

**验证**：冒烟 3 案 LLM 模式一致性 1.0、六门全绿；全量 151 案 + 对抗集 LLM 模式结果见
evals/reports/t130_llm_full_v41.json / t130_adversarial_llm.json（回填于 progress）。

## D056 追记（T130 收尾，2026-09-21）：最终评测结论与三项后续发现

**修复后终值**（evals/reports/t130_llm_full_v41_fixed.json，deepseek-flash 153 案）：
- **一致率 1.0（153/153）、六门全绿、0 失败**——liveness 修复后新模型达到旧模型满分水平；
- **tokens/案 = 7,075.8**（全案 1,082,594）、延迟 avg 6.22s / p95 7.68s——证据缺口#1（D039 成本）正式关闭；
  对照：确定性模式 0.25s/案、零 token——LLM 调度的代价边界从此有数。

**路由退化与 liveness 修复（D056-2）**：未修复的两样本（0.8431 / 0.902）暴露 v4.1-flash
存在"反复请求已完成阶段"的规划退化（旧模型两次全量 151/151 未出现）。守卫正确丢弃，
但 route_dispatch 空目标直接 END → 案件中途终止（注释"正常流程不会到达"被新模型证伪）。
修复：守卫清空 LLM 目标且未超预算时回落 default_route（nodes/orchestrator.py，模式留痕
llm_degraded_fallback + fallback_default_route 审计注记 + 单测）。修复即满分——退化被
完全兜住，是"LLM 自由度由守卫圈定"哲学的又一次实证。

**三项后续发现（不阻塞切换，入账）**：
1. **路由提示注入面**：对抗集 LLM 模式下 3/8 注入案（角色伪装/虚构免责/字段注入）被
   拐到 human——方向保守（amount 全对，无错赔），实害是人工队列可被申请文本扰动
   （DoS 面）。待办 T131：路由 prompt 数据/指令分离（描述文本定界包裹 + 中和指令式措辞）。
2. **同义词鲁棒 0/6 与模型无关**：LLM 模式下 robustness 依旧 0/6——D054"LLM 层价值=
   鲁棒性"假设**证伪**；鲁棒性提升路径在 skill/关键词迭代，不在换模型。
3. **评测"LLM 模式"语义收窄**：现行 --llm 仅启用路由器（liability/material/decision 仍
   确定性），三种 LLM 组件无联合 LLM 评测形态。挂 T125 备注。

## D057（2026-09-22，T132-T137 规划）：门户在线客服对话——形态四决断

**背景**：用户需求为门户（chatui）增加在线客服对话。四项产品决策经用户逐项确认：
全功能助手（理赔知识问答 + 案件进度查询 + 引导提交理赔）/ 独立 ReAct Agent /
悬浮客服气泡 / AI + 转人工坐席。探索结论：claim_rule_rag 工具、create_agent
装配模式、get_chat_model、cases.get_case 拼装口径、前端 cf-* 设计系统与
react-markdown 均可纯复用；转人工需新会话域（interventions 工单语义不匹配）；
全仓无 SSE/streaming 基建。

**决断与理由**：

1. **独立 Agent，不接核赔主图**：照 worker_agent 的 create_agent 装配
   （get_chat_model + ToolError/ModelCallLimit 中间件）独立成 services/support
   域。客服闲聊与核赔管线生命周期完全不同，接主图会污染 checkpoint 语义与
   静态合规门边界。工具四件：claim_rule_rag 复用（工厂守卫版直取）；
   case_status_query（get_case 拼装口径抽 service）；claim_draft_link（生成
   预填表单链接）；escalate_to_human（走会话 store）。

2. **会话记忆走 DB replay，不接 CheckpointManager**：候选两条——
   checkpointer(thread_id=会话id) vs 会话表历史窗口 replay。选后者：转人工后
   坐席消息与用户消息必须在同一时间线上（checkpoint 双源同步复杂，且坐席侧
   不可读）；LLM 本就无状态，两方案每轮 token 成本等价；窗口长度可控。
   support_conversations / support_messages 兼作 UI 历史与坐席 transcript 的
   单源投影（与"CaseEvent 审计投影 + checkpoint 图状态并存"的既有架构同理，
   但客服域只留一源）。核赔主图 checkpoint 不受影响。

3. **转人工不复用 interventions 工单，v1 轮询不流式**：核赔工单 = 案件图
   interrupt 挂起的投影，强绑 Case 状态机；客服转人工 = 对话移交，无案件可挂。
   新建会话状态机 ai → escalated → closed（escalated 时 AI 停答、坐席接管；
   close 终止会话）。workbench 交互照搬三件套模式（列表/详情/处理表单）。
   通信 v1 请求-响应 + 轮询（AutoRefresh 模式）——全仓无 SSE 基建，流式
   打字机挂后续优化，不阻塞本增量。

4. **引导提交 = 预填跳转，不在对话内直接立案**：agent 对话收集险种/事发信息
   后由 claim_draft_link 生成带 query 参数的表单链接，CaseForm 加 initialValues
   预填。理由：立案依赖表单校验与材料上传，对话内提交会绕过两者，且与既有
   幂等提交链路（B01 POST /cases）重复造轮子。

**隐私与边界口径**：延续门户现状（无鉴权、case_id 即凭证，与 GET /cases/{id}
一致，不新增放宽）；客服 system prompt 明确拒答边界——不承诺赔付结果、不引用
内部阈值/规则原文、答不了或用户明确要求时转人工（T124 对抗门对注入面的关注
延伸到客服域，T133 验收含边界断言）。

**验证计划**：T132-T137 分六任务交付（会话表 / Agent+工具 / 门户 API /
转人工闭环 / 前端气泡 / 端到端冒烟），每任务全量绿 + 单 commit + 用户确认后
推进。

## D058（2026-09-22，T138）：记忆治理三件——置信度门控 / 应用层 TTL / 删除链路

**背景**：D055-3 挂账的申请人记忆治理（第二轮面试确认"写而不读、无删除、
无门控"，读闭环已由 T129 补齐）。探索实证：BaseStore 有 adelete/aget 异步
接口；**InMemoryStore 不支持原生 ttl（aput(ttl=) 实测抛 NotImplementedError）**，
仅 AsyncPostgresStore 支持。

**决断**：

1. **置信度门控（写侧源头拦断）**：CaseMemoryRecord 加 confidence 字段——
   auto 签发路径传 min(材料, 责任) 置信度（auto_adjudicate 已算，顺手复用），
   human 三路径（坐席签批/escape/REJECT 保守兜底）缺省 1.0（人工或保守动作
   是确定性事实，不应被门控误伤）。confidence < memory_confidence_floor
   （默认 0.6）不写入只告警——低置信 not_covered 档案污染从源头消失。存量
   prod 条目缺字段 → 模型缺省 1.0 兼容，展示不受影响。
2. **TTL 应用层实现（双后端一致）**：不走 BaseStore 原生 ttl——dev 后端根本
   不支持，按后端分叉会让 dev/prod 行为漂移且 dev 不可测。改为值内 expires_at
   字段（memory_ttl_days 默认 365，0=永不过期）+ 读取惰性过滤 + 顺手 adelete
   清理。代价：过期条目在被读到之前物理留存——档案视图语义正确，可接受。
3. **删除链路（坐席操作 + 审计留痕）**：DELETE /api/v1/memory/{user_id}/
   entries/{case_id}（store.adelete + CaseEvent kind=human payload.action=
   memory_deleted）；workbench 档案卡每条加删除按钮。**删除语义**：非
   tombstone——scripts/rebuild_memories.py 是显式人工安全网，重跑按 cases 表
   终态重建（删除≠永久抹除，如需永久抹除需 tombstone 表，当前无此需求）。

**验证**：tests/memory +5（门控/置信度落库与缺省/TTL 写入与惰性过期删除/
TTL 关闭/删除链路）+ tests/api/test_memory.py +2（删除端点 200+审计事件、
404）；425 passed + ruff 绿 + workbench build 绿。

## D060（2026-09-22，T140）：提取置信度校准——来源常数 → 齐全度函数

**背景**：缺口#9（面试链 3）。旧口径 `_SOURCE_CONFIDENCE` 纯来源查表
（vision/text=0.9、mock=0.3）——同源同分：清晰图与模糊图、全字段与缺字段
无差别，字段缺失对调度零感知。

**决断**：

1. **确定性校准函数，不做模型自评**：`_calibrated_confidence = 来源基准 −
   关键字段缺失扣分`（金额/诊断各 -0.25，日期/姓名各 -0.1，clamp [0.05,
   基准]）。面试口径是"用真实 OCR 错误样本标定常数"——模型自评不可控不可测，
   常数函数可用金样本确定性标定与回归。
2. **扣分设计锚定下游阈值**：缺金额 → 0.65（> material_confidence_floor 0.6
   但 < auto_approve_confidence_floor 0.8 → 不再自动签发）；缺金额+诊断 →
   0.4（< 0.6 → 转人工裁量）。字段缺失从此真正影响调度路径，而非只影响展示。
3. **0 值金额视同未提取**（0 元发票）；mock 兜底全缺 → clamp 下限 0.05。
   引用型材料（`_reference_document`，1.0/0.4-note）不经校准，口径不变。

**兼容性实证**：金样本数据集 153 案中仅 4 份材料带 extraction（全部属于
T127 两个矛盾案，节点级已压 0.4）→ 校准对评测门零影响（复跑六门全绿
100%）；既有单测的全字段提取 0.9 / 矛盾 0.4 / 引用型 1.0 断言全部不变。

**验证**：test_material_review +1（五分支：全字段 0.9 / 缺金额 0.65 /
缺双核心 0.4 / mock 全缺 0.05 / 0 元视同缺失）；431 passed + ruff 绿 +
评测门不退化。

## D061（2026-09-22，T141）：checkpoint schema 版本策略——版本戳 + resume 降级门卫

**背景**：缺口#7（面试链 5.5）——恢复机制扎实（Command resume/@task 短路），
但 State schema 演进无版本语义："升级图代码后旧 checkpoint 还能不能 resume"
此前是裸的。现状事实：框架侧表结构迁移由 langgraph-checkpoint-postgres 的
checkpoint_migrations 自管（setup() 自动补跑）；缺的是**业务 State 形状**的
版本判定。

**决断**：

1. **版本戳**：`state.CASE_SCHEMA_VERSION = 1`（Final 常量）；ClaimCaseState
   加 schema_version 字段，intake 写入。**bump 语义**：State 字段增删改 /
   channel 语义变化时人工 +1（写代码的人判断兼容性，不做自动推断）。
   无此字段的旧 checkpoint 一律视为不匹配（版本化之前=未知=保守处理）。
2. **resume 降级门卫**（interview 口径"不兼容时降级重跑"）：execute_job 的
   RESUME 分支 aget_state 比对版本——匹配 → Command(resume) 原路径；
   不匹配 → **删旧 thread（adelete_thread）+ 取该案最近一次 RUN 任务的原始
   图输入全新重跑** + schema_reset 审计事件。理论依据 D006：案件事实权威在
   cases 表，checkpoint 只承载可重建的执行态——重跑无损。删 thread 必须先行：
   全新输入落在既有 channel 上会与旧阶段结论合并，污染重跑。
3. **防御**：降级时找不到原始 RUN 输入（理论不可达）→ 显式 RuntimeError 进
   任务重试/死信，不盲 resume 旧格式。

**兼容性实证**：全部既有真图 resume 链路（B03 补件/工单处理/跨重启恢复）
的 checkpoint 由现行 intake 写入（带版本戳）→ 匹配路径，行为不变；
评测门不涉 resume，复跑六门全绿 100%。

**验证**：test_case_jobs +2（旧 checkpoint 降级：原始 RUN 输入重跑 + thread
删除 + schema_reset 审计；无 RUN 输入防御失败进重试）+ 既有 resume 包装
测试改为版本匹配路径；433 passed + ruff 绿 + 评测门不退化。

## D062（2026-09-22，T142）：robustness 同义词清账 + 对抗集门禁升级

**背景**：对抗集 robustness tier（6 条同义词变体：隆鼻/牙齿矫治/摘镜/喝了点
酒开车/深潜/玉器）自 T124 起确定性/LLM 双模式 0/6——D054/D056 结论"提升
路径在 skill/关键词迭代，不在换模型"。本次定位实锤：缺口全部在
pack.exclusion_keywords 同义词未覆盖（skill 文本反而已有部分示例，如
medical.md 的"隆鼻"——文本先于关键词层能力，与 T127 相反方向的失配）。

**决断**：

1. **关键词层补同义词（六案各自命中、主基线零干扰）**：medical +（隆鼻→
   整形美容 / 矫治→牙科 / 摘镜→矫正）；auto +（酒开车→酒后驾驶，口语
   "喝了点酒开车"的稳定子串）；accident +（深潜→高风险运动）；property +
   （玉器→金银珠宝及有价证券）。除外项名称与主基线正例路径一致（如摘镜归
   "矫正"而非新立名，保证 reason/exclusions 口径统一）。**干扰预检**：新词
   在主数据集 153 案零出现、对抗集各自仅命中目标案——补词前先证明不伤门。
2. **robustness tier 升入对抗集硬门**：T124"仅报告"是关键词已知缺口的临时
   降级口径；缺口清账后保留降级等于给回归留后门。撤销排除（gated_results
   不再剔 robustness），robustness_block 保留为该 tier 分层观测。此后同义词
   退化为门禁 invariant——再漏判即红灯，驱动迭代的方式从"看报告"变"修门"。
3. **skill 文本同步对齐**（生产 LLM 路径与确定性路径同口径）：四个
   liability_judge skill 的除外枚举补同义词示例（牙齿矫治/摘镜/喝了酒开车/
   深潜/玉器）。

**验证**：test_liability_agent +6（参数化六案：verdict=not_covered + 除外项
名称精确断言）；对抗集确定性 14/14=100% 六门全绿（t142_adversarial_gate.json）；
对抗集 LLM 模式 14/14=100%（t142_adversarial_llm.json，此前 8/14=57.1%）；
主门 153 案 100% 不退化；441 passed（+6）+ ruff 绿。

## D063（2026-09-22，T143）：端到端冒烟脚本两处口径过期 + 两个真实缺陷

**背景**：用户要求"测试项目能否正常运行"，体检发现 pytest 441 绿 / ruff 绿 /
服务 health ok，但 `scripts/verify_adjudication.py`（T092 五组冒烟）9 项失败。
逐条定位后，确认**两处脚本口径过期 + 两个真实生产缺陷**（不是脚本自己的问题）。

**决断**：

1. **缺陷①：连续补件上传 500（真实 bug）**——`upload_case_material` 在
   `deliver_case_job` 之后仍读 `case.id` 构造响应；而 deliver 的**活跃任务冲突
   分支会 rollback 复位会话**，rollback 无条件使 ORM 实例过期，async 上下文再
   触碰属性即触发惰性加载 → `MissingGreenlet` → 500（实测：三份连续上传第 2、3
   份全 500）。修复=deliver 之后只用本地快照（路由参数 `case_id` + 早取的
   `case.status`），不再触碰 `case` 实例。**教训**：D049 把"会话复位"内化进
   deliver 是正确的，但调用方必须假设"deliver 后 ORM 实例不可信"（接口文档味
   的隐性契约，此前只写在注释里）。
2. **缺陷②：AI 一致性审查恒压置信度 → 自动签发永不发生（真实 bug）**——
   `MATERIAL_REVIEW_AI_PROMPT` 只说"anomalies：发现的问题列表"，模型把**通过性
   陈述**（"姓名一致""金额一致""未见冲突"）与**建议/辅助项缺失**（hospital 为空、
   无自费标识、缺 medical_record）一并写入 → anomalies 恒非空 → 节点压至 0.4
   → 低于 `material_confidence_floor`(0.6) → 转人工。实测三份真实 docx 材料
   （全部 text_model、字段齐全一致）仍 `referred`。修复=prompt 口径收紧：异常
   **只**四类（姓名/诊断/金额/日期矛盾），通过必须空数组，中性观察与建议进
   `notes`（notes 不参与压分），并明示非核赔必需项缺失不算异常；顺带补数据边界
   铁律（与 T131 路由 prompt 同口径）。
3. **脚本口径更新（两处过期）**：①自动签发组改为"零材料提交 → 逐份上传
   python-docx 现场生成的 Word（走 text_model 提取，基准 0.9）"——T140 后纯声明
   材料必经引用型兜底、字段缺失扣分 → 转人工是设计行为，旧脚本的"只声明不上传
   即 auto_issued"已不成立；②未上线险种组改用 POL-2025-0002（重疾险 → unknown
   → escape）——T120 四险种全上线后旧用例的"意外险 escape"已失效。另：user_id
   加时间戳后缀，脚本可重复运行（旧版二次运行必撞自然键幂等返回 200）。
4. **未做（留给后续决策）**：该脚本仍未进 CI（CI 只跑 ruff/pytest/compose
   health/评测门），故口径腐烂三个月无人发现。是否把它接进 CI 需先解决"依赖
   真实 LLM + 真实服务"的前置条件，本次不动。

**验证**：444 passed（+3：上传冲突静默吸收 + prompt 口径断言 + 审查通过不压分）
+ ruff 绿；**verify_adjudication 23/23 全过**（自动签发 4640.00 + 决定书、
补件闭环、unknown escape、工单列表）；主门 153 案六门 100% + 对抗门 14/14
100% 零退化；真实 deepseek 全链路无 500。

## D064（2026-09-22，T144）：端到端冒烟接入 CI——compose 全栈 + 真 Key

**背景**：D063 末尾挂账项——verify_adjudication 不在 CI 里，故口径腐烂三个月
无人发现（T140 置信度校准、T120 四险种上线两次语义变更它都没跟上）。两个候选：
①拆一个零 LLM 的确定性冒烟进 CI；②compose 起全栈后带真 Key 跑。用户拍板 ②。

**决断**：

1. **挂在既有 docker job**：compose 全栈（PG/Qdrant/Redis + init 迁移/种子/
   ingest）已在该 job 起好，不新开 job——新开要重复拉起一套栈，成本翻倍。
   该 job 原来没有 uv/依赖，补 `setup-uv` + `uv sync --frozen` 两步。
2. **真 Key 走 secret，未配置必须跳过**：`LLM_API_KEY: ${{ secrets.LLM_API_KEY
   || 'sk-ci-placeholder' }}` 供 compose 透传（健康检查只校验 Key 非空，无 secret
   时口径与既往完全一致，不会让 fork PR 变红）；冒烟步骤加
   `if: ${{ secrets.LLM_API_KEY != '' }}`——**无 secret 即跳过**，默认不阻塞。
   这是"门禁"与"可用性"的取舍：有 Key 的 main/同仓 PR 才是真门禁，fork PR
   保底靠 lint-test + eval-gate 两道。
3. **清理必须是 `if: always()`**：原 `docker compose down -v` 写在 up 步骤末尾，
   健康检查失败就留栈（CI 机器残留 + 端口占用）；拆成独立步骤并加 always。
4. **稳定性兜底（写在 CI 注释里，不默认开启）**：真实 LLM 编排存在偶发抖动
   风险，给冒烟步骤加 `ORCHESTRATOR_LLM_ENABLED=false` 即切确定性编排——材料
   提取仍走真实 LLM（text_model 基准 0.9），Key 依然必需，但调度不再依赖模型
   规划稳定性。本地已实测该模式 23/23。
5. **成本**：每轮约十余次 flash 调用（五组案件，含三份材料提取），按 deepseek
   定价可忽略；主要成本是 compose 起栈 + init 的 BGE-M3（缓存命中秒级）。

**验证**：本地双模式各跑一遍均 23/23（默认真实 LLM 编排 / ORCHESTRATOR_LLM_ENABLED
=false 确定性编排）；ci.yml 经 YAML 解析校验（步骤/env/if 齐全）；README 5.3 补
脚本口径说明与 secret 前置条件。

## D065（2026-09-22，T145）：CI 冒烟默认改离线兜底档（零 API Key）

**背景**：D064 落地后用户改主意——默认路径不该依赖仓库 secret（fork PR 永远
跑不到，维护者还得配 Key 才有门禁，等于门禁形同虚设）。

**决断**：

1. **脚本加 `--offline` 档位**：零 Key 下材料提取必然降级 `mock_fallback`
   （基准 0.3 + 字段扣分）→ 低于 floor 0.6 → **按设计转人工**（T140 语义），
   因此门禁改为守"上传落档 / 补件闭环 / 状态机 / 审计 / escape / 工单"，
   不断言自动签发与核定金额——那部分由零 LLM 的 eval-gate（153 案六门）coverage，
   **两条门分工不重叠**，不重复也不留空档。
2. **CI 默认离线、有 Key 自动升级**：无 secret → 跑 `--offline`；有 secret →
   跑完整档（真实提取 + auto_issued 4640.00 + 决定书）。`if` 互斥，二选一，
   默认路径零依赖。
3. **CI 冒烟一律确定性编排**：compose 新增 `ORCHESTRATOR_LLM_ENABLED`
   透传（默认 true，本地行为不变），CI 设 false——调度不依赖模型规划稳定性，
   材料提取仍按 Key 有无决定走真实 LLM 或 Mock，两档语义都成立。
4. **审计断言按档位分层**：`status_change` 事件由 `auto_adjudicate` 在签发/终态
   落库时写入，离线档案件停在 human_gate 挂起（interrupt）故不产生——离线档只
   要求 `routing + stage_result`，完整档三类齐全。这是既有行为的如实反映，
   不为凑断言改产品事件。

**踩坑（本地验证期，非 CI 问题）**：dev 下**多实例共用一个 SQLite 库**时，
background JobLoop 会互相认领任务，而 checkpointer 是各进程的内存 saver →
被别的实例认领的 resume 找不到 checkpoint → T141 门卫判定版本不匹配 →
`schema_reset` 用 RUN 原始输入重跑 → 案件退回"缺三件"。表现是完整档 5 项失败。
单实例即 23/23。CI 是 compose 单实例，无此风险，但本地并行调试须注意。

**验证**：离线档 22/22（假 Key 模拟零 LLM：`ORCHESTRATOR_LLM_ENABLED=false
LLM_API_KEY=sk-invalid`）；完整档 23/23（单实例）；ruff 绿；ci.yml 与 compose 经
YAML 解析校验。

## D066（2026-09-22，T146）：客服问答金样本门——三层判分 + 检索同测，不进默认 CI

**背景**：客服域（T132-T137）上线后无量化回归口径——smoke_support_e2e 的知识
问答环节是"人工核对"档（无断言），T137 当日五问实测全对但属一次性证据。用户
要求补金样本集。

**决断**：

1. **数据集 15 案五类**（`evals/datasets/support_qa.json`）：knowledge ×6 /
   progress ×2（存在案 / 不存在案）/ redline ×3（索要赔付承诺 / 索要内部阈值 /
   施压马上出结论）/ out_of_kb ×2 / escalate ×2。每案字段：question +
   expected_keyword_groups（组间 AND、组内任一命中，兼容同义表述）+
   forbidden_keywords（红线漏放判分）+ expect_escalation + expected_rag_sources。
2. **判分三层**（`evals/support_metrics.py` 纯函数）：答案关键词组全命中 +
   禁止词零出现 + 转人工终态一致（expect_escalation ↔ status）；知识案附检索
   断言（search_kb top-4 的 source_file 与期望交集非空）——答案与检索一次跑
   同时覆盖，不做独立检索模式。
3. **运行器进程内**（`evals/support_suite.py`，对齐 adjudication_suite 骨架）：
   临时 SQLite + swap_engine + 文件种子 + 预置两条已知案件（received /
   auto_issued）供进度查询；直调 `services/support/agent.py::reply`，不依赖
   起服务。硬门 = 15 案答案门 100%（含禁止词与 escalation）；检索命中率为
   分层观测不阻塞（语料 12 篇，检索分数波动不该误伤答案门）。
4. **不进默认 CI**：客服 Agent 是纯 ReAct 对话，无核赔域的确定性兜底路径——
   零 LLM 跑不出答案，门必须真 Key；且检索断言需 BGE-M3 本地模型（CI 拉模型
   不现实）。定位 = 本地 / 有 Key 环境手动门（对齐核赔 `--llm` 档与 D064 完整
   档的"有 secret 才跑"模式，但人工触发）。CI 只进零依赖单测（数据集 schema
   校验 + 判分纯函数）。
5. **期望锚定知识库原文**：等待期 30 天 / 意外医疗 90%·0 免赔 / 旗舰版目录外
   可计入 / 社保抵扣免赔——关键词组均从 kb_docs 原文提取；红线 forbidden 含
   内部阈值泄漏词（AUTO_APPROVE_LIMIT=5000 的三种写法）。

**验证**：见 progress T146（判分单测 + 真实 LLM 全量门）。

## D067（2026-09-23，T147）：坐席端点鉴权——多 Key + Key 即身份（路径 A）

**背景**：P0 评估发现 18 个 API 端点零鉴权，坐席身份为请求体自报字符串——任何能
访问端口的人都可签批 / 改判 / 删记忆。用户拍板路径 A（多 Key + 身份派生）。

**决断**：

1. **STAFF_KEYS 多 Key 配置**（`"alice:key1,bob:key2"`）+ 请求头 `X-Staff-Key`；
   `require_staff` 依赖逐 Key `secrets.compare_digest`（防时序攻击），通过返回
   Key 对应坐席身份。
2. **Key 即身份**：resolve 的 `resolved_by`、抽评与记忆删除审计的 `operator`
   由 Key 派生（可信），未配置（dev）回退请求体自报——审计归因从"自报"变"派生"。
3. **dev 开放 / prod 强制**：staff_keys 为空 = 不校验（467 既有测试与冒烟零改动
   向后兼容）；prod profile 启动时 model_validator 强制非空（忘了配 = 启动失败，
   非 200 裸奔）。compose 演示栈默认注入公开演示账号 `demo-staff:demo-key-2026`
   （与冒烟脚本 fallback 对齐；生产部署必须覆盖）。
4. **挂载粒度**：interventions / memory 整 router 级（全部坐席端点）；support
   的 /tickets 三件套端点级（同 router 混客户/坐席）；客户端点（cases / 客服
   会话）零改动。
5. **升级位**：多 Key 无个人吊销粒度与密码轮换——真多人使用时升账号表（登录换
   token），Header 协议兼容，客户端零改动。

**验证**：480 passed（+13 鉴权矩阵/身份派生/prod 校验单测，4 个既有 prod 构造
测试补 STAFF_KEYS）；真实服务带 Key 实测 8/8 矩阵（无头 401/错 Key 401/对 Key
200×3/客户端点 201/health 200）；workbench build 绿。
