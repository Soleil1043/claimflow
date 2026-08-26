"""应用配置模块。

通过 pydantic-settings 从环境变量 / .env 文件读取配置，
按 APP_PROFILE（dev | prod）切换依赖的部署形态（见 decisions.md D005）：
- dev：SQLite(aiosqlite) + Qdrant local mode + 内存缓存（零容器，本地开发）
- prod：PostgreSQL + Qdrant 服务 + Redis（交付架构，不降级）
"""

from enum import StrEnum

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    # 主链路模型（意图/规划/工具调用/生成）；旧别名 deepseek-chat 已退役（D007）
    llm_model: str = "deepseek-v4-flash"
    # 图片 OCR 专职模型，失败降级 Mock（D008）
    llm_vision_model: str = "deepseek-v4-flash-vision-exp"

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

    # ===== 长期记忆（T034/T035，architecture.md 6.3） =====
    # 会话摘要写入记忆 collection 的开关；每 N 轮（用户消息数）触发一次摘要更新
    memory_enabled: bool = True
    memory_summary_every_n_turns: int = 3
    qdrant_memory_collection: str = "long_term_memory"
    # 读注入（T035）：首轮检索条数与相似度下限（低于 min_score 视为噪声不注入）
    memory_top_k: int = 2
    memory_min_score: float = 0.4

    # ===== Redis（prod） / 内存缓存（dev 降级） =====
    redis_url: str = "redis://localhost:6379/0"

    # ===== 工具结果缓存（T028） =====
    # 幂等工具白名单：纯读查询类，相同入参结果确定
    tool_cache_enabled: bool = True
    tool_cache_ttl_seconds: int = 300
    tool_cache_tools: str = (
        "policy_query,medical_record_query,diagnosis_matcher,claim_rule_rag,claim_status_query"
    )

    # ===== 轮次 Token 预算（T029） =====
    # 单轮对话（意图→规划→执行→生成→合规）总 token 上限；超限只告警日志，不阻断。0=不设预算
    turn_token_budget: int = 0

    # ===== OTel 追踪（T039，D015 后置项） =====
    # 开关（默认关：不起 tracing 栈时零开销）；OTLP gRPC 上报地址；采样率 0.0-1.0
    otel_enabled: bool = False
    otel_endpoint: str = "http://localhost:4317"
    otel_sampling_ratio: float = 1.0

    # ===== Embedding（BGE-M3，本地 sentence-transformers） =====
    embedding_model: str = "BAAI/bge-m3"
    embedding_device: str = "cpu"

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
