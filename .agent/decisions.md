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
