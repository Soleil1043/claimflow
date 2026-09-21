"""应用配置模块。

通过 pydantic-settings 从环境变量 / .env 文件读取配置，
按 APP_PROFILE（dev | prod）切换依赖的部署形态（见 decisions.md D005）：
- dev：SQLite(aiosqlite) + Qdrant local mode + 内存缓存（零容器，本地开发）
- prod：PostgreSQL + Qdrant 服务 + Redis（交付架构，不降级）
"""

from decimal import Decimal
from enum import StrEnum

from pydantic_settings import BaseSettings, SettingsConfigDict

from schemas import contract


class Profile(StrEnum):
    """运行 profile：dev=本地降级开发模式，prod=全量真实依赖。"""

    DEV = "dev"
    PROD = "prod"


class Settings(BaseSettings):
    """全局配置，字段与 .env.example 一一对应。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ===== 应用 =====
    app_profile: Profile = Profile.DEV
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # ===== LLM（DeepSeek，OpenAI 兼容接口） =====
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    # 主链路模型（意图/规划/工具调用/生成）
    llm_model: str = "deepseek-flash"
    # 图片 OCR 专职模型，失败降级 Mock（D008）
    llm_vision_model: str = "deepseek-flash"

    # ===== 材料上传（T049，D024：PDF/Word 支持） =====
    material_max_size_mb: int = 10
    # 扫描件 PDF 最多渲染页数（走 vision OCR）
    material_pdf_render_pages: int = 3
    # PDF 抽取文本低于该字符数视为扫描件（转渲染）
    material_pdf_text_min_chars: int = 50

    # ===== PostgreSQL（prod） / SQLite（dev 降级） =====
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "claim"
    postgres_password: str = "claimpass"
    postgres_db: str = "claim_agent"

    # ===== Qdrant =====
    qdrant_url: str = "http://localhost:6333"
    # dev profile 下生效的 local mode 路径（D001/ADR-004）
    qdrant_local_path: str = "./data/qdrant"
    qdrant_collection: str = "claim_rules"

    # ===== GraphRAG（T032，D017 轻量混合召回） =====
    # 知识图谱增强检索开关；false 时 rag 路径行为与引入前完全一致
    graph_rag_enabled: bool = True

    # ===== 重排序精排（T043，D020） =====
    # 开关默认关：小语料（53 chunk）下收益趋近于零（T033 结论），作为"语料扩大后"的预留层
    rerank_enabled: bool = False
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_device: str = "cpu"
    # 后端：torch（默认，稳定）| onnx（INT8 动态量化 ~200MB，首次启用时自动导出）
    rerank_backend: str = "torch"
    # 精排流水线：先召回 rerank_recall_k 条 → CrossEncoder 重排 → 取 rerank_top_k 条
    rerank_recall_k: int = 8
    rerank_top_k: int = 4

    # ===== 申请人记忆（T100） =====
    # 记忆写读总开关（旁路路径，关断零影响）
    memory_enabled: bool = True
    # 申请人记忆注入 orchestrator 路由快照：默认关——LLM 路由口径与金样本
    # 评测保持一致，观察检索质量后再开启
    memory_in_routing: bool = False

    # ===== Redis（prod） / 内存缓存（dev 降级） =====
    redis_url: str = "redis://localhost:6379/0"

    # ===== 工具结果缓存（T028） =====
    # 幂等工具白名单：纯读查询类，相同入参结果确定（名称须为工厂注册名）
    tool_cache_enabled: bool = True
    tool_cache_ttl_seconds: int = 300
    tool_cache_tools: str = "policy_query,record_query,diagnosis_matcher,claim_rule_rag"

    # ===== 轮次 Token 预算（T029） =====
    # 单轮对话（意图→规划→执行→生成→合规）总 token 上限；超限只告警日志，不阻断。0=不设预算
    turn_token_budget: int = 0

    # ===== 核赔分级自动阈值（Phase 8，D037/D039） =====
    # 自动签发金额上限：核定金额 ≤ 该值且低风险才允许自动出《理赔决定书》
    # （规范值由 schemas.contract 承载，T097；此处默认引用契约，可经环境变量覆盖）
    auto_approve_limit: Decimal = contract.AUTO_APPROVE_LIMIT
    # 自动签发要求的全链路最低置信度（各阶段结论置信度门槛）
    auto_approve_confidence_floor: float = 0.8
    # 材料抽取置信度下限：低于该值转 orchestrator 异常裁量（而非直接补件）
    material_confidence_floor: float = 0.6
    # 合规 MODIFY 修订闭环轮数上限（防死循环，D012 语义）
    compliance_max_rounds: int = 2
    # orchestrator 每案件调度调用预算（超限告警并强制收敛；D039 防绕圈；规范值 schemas.contract）
    routing_call_budget: int = contract.ROUTING_CALL_BUDGET
    # 自动签发案件人工抽检比例（试点期质量兜底，0=关闭）
    manual_review_sample_rate: float = 0.05
    # orchestrator 路由是否启用 LLM 决策（False=纯确定性兜底编排；测试/降级用）
    orchestrator_llm_enabled: bool = True
    # 材料审核 AI 一致性审查（skill 装配；False=仅规则层，测试/降级用）
    material_review_llm_enabled: bool = True
    # 责任认定 ReAct Agent（False=关键词规则兜底，测试/降级用）
    liability_llm_enabled: bool = True
    # 决定书叙述 LLM 撰写（False=纯代码模板叙述，测试/降级用）
    decision_writer_llm_enabled: bool = True

    # ===== 案件交付队列（T103，D044 混合方案：任务表凭证 + 常驻单消费者） =====
    # 执行档位：background=常驻循环异步执行（生产默认，POST 受理即返回）；
    # inline=请求内同步执行（测试/兼容档，响应保留同步终态语义）
    case_jobs_execution: str = "background"
    # 认领轮询间隔（秒）——受理到开跑的最大额外延迟
    case_jobs_poll_interval_s: float = 0.5
    # 单任务最大尝试次数（1=不重试）
    case_jobs_max_attempts: int = 3
    # 退避基数（秒）：第 n 次失败后延迟 base * 2^(n-1)
    case_jobs_backoff_base_s: float = 2.0
    # 关停排水超时（秒）：超时放弃在飞任务（行留 running，下次启动回收）
    case_jobs_drain_timeout_s: float = 30.0
    # 案件材料上传落盘目录（B03；storage_path 供材料审核真实提取）
    case_materials_dir: str = "./data/uploads"

    # ===== 门户客服（T132，D057） =====
    # Agent 多轮记忆 replay 窗口：送入 LLM 的最近消息条数（含用户与 AI 双方，超出截断）
    support_history_window: int = 20

    # ===== OTel 追踪（T039，D015 后置项） =====
    # 开关（默认关：不起 tracing 栈时零开销）；OTLP gRPC 上报地址；采样率 0.0-1.0
    otel_enabled: bool = False
    otel_endpoint: str = "http://localhost:4317"
    otel_sampling_ratio: float = 1.0

    # ===== 跨供应商 A/B 变体（T041） =====
    # GLM（智谱）OpenAI 兼容接口；供 evals/variants.py 的 glm-5.3-flash 变体经
    # $ 字段间接引用（$glm_api_key / $glm_api_base_url）切换供应商
    glm_api_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    glm_api_key: str = ""

    # ===== Embedding（BGE-M3，本地 sentence-transformers） =====
    embedding_model: str = "BAAI/bge-m3"
    # 本地模型目录（优先于 embedding_model 的 repo id）：容器/离线场景直载快照目录，
    # 绕开 huggingface_hub 缓存校验（Windows 拷入的真实文件快照在 Linux 会被判无效缓存）
    embedding_model_path: str | None = None
    embedding_device: str = "cpu"
    # HF_HUB_OFFLINE 导出开关（HF_OFFLINE=1）：模型已入本地缓存时跳过 HuggingFace 在线
    # 版本检查——网络不可达时该检查会超时重试 5 轮，拖垮 API/evals 启动（README 已知坑）
    hf_offline: bool = False

    # ===== 日志 =====
    log_level: str = "INFO"

    @property
    def is_prod(self) -> bool:
        """是否生产 profile。"""
        return self.app_profile == Profile.PROD

    @property
    def database_url(self) -> str:
        """业务库 SQLAlchemy 异步连接串：prod=PostgreSQL(asyncpg)，dev=SQLite(aiosqlite)。"""
        if self.is_prod:
            return (
                f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        return "sqlite+aiosqlite:///./data/claim_agent.db"

    @property
    def checkpoint_conn_string(self) -> str:
        """LangGraph checkpoint 连接串（psycopg 协议，prod 用；dev 走 MemorySaver 不使用）。"""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def _url_for_log(self) -> str:
        """日志用的脱敏连接串（隐藏密码）。"""
        if self.is_prod:
            return (
                f"postgresql://{self.postgres_user}:***@"
                f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        return self.database_url


# 模块级单例：应用内统一 `from app.core.config import settings` 获取
settings = Settings()

if settings.hf_offline:
    # huggingface_hub 在 import 时即读取 HF_HUB_OFFLINE（constants 模块级求值），
    # 必须在任何模型加载前导出；config 是全项目最早被 import 的模块，
    # 由此覆盖 API（uvicorn）/ evals（评测套件）/ 脚本全部入口。
    # setdefault：显式 shell 环境变量优先。
    import os

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
