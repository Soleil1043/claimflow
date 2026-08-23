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
