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
