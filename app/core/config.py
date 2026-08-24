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

    # ===== Redis（prod） / 内存缓存（dev 降级） =====
    redis_url: str = "redis://localhost:6379/0"

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
