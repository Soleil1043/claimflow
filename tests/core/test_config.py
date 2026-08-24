"""app.core.config 单元测试。"""

from app.core.config import Profile, Settings


def test_default_settings_load_from_env_example_fields() -> None:
    """默认配置可加载，且关键字段与 .env.example 语义一致。"""
    s = Settings(_env_file=None)
    assert s.app_profile == Profile.DEV
    assert s.llm_base_url == "https://api.deepseek.com"
    # 主链路模型（D007：deepseek-chat 旧别名已退役，禁止回退）
    assert s.llm_model == "deepseek-v4-flash"
    # OCR 专职视觉模型（D008 混合策略）
    assert s.llm_vision_model == "deepseek-v4-flash-vision-exp"
    assert s.qdrant_collection == "claim_rules"
    assert s.embedding_model == "BAAI/bge-m3"


def test_database_url_switches_by_profile() -> None:
    """dev → SQLite(aiosqlite)；prod → PostgreSQL(asyncpg)。"""
    dev = Settings(_env_file=None, app_profile="dev")
    assert dev.is_prod is False
    assert dev.database_url.startswith("sqlite+aiosqlite:///")

    prod = Settings(_env_file=None, app_profile="prod", postgres_password="secret")
    assert prod.is_prod is True
    assert prod.database_url.startswith("postgresql+asyncpg://")
    assert "secret" in prod.database_url
    # checkpoint 连接串走 psycopg 协议（不带 +asyncpg 后缀）
    assert prod.checkpoint_conn_string.startswith("postgresql://")


def test_settings_read_from_env_vars(monkeypatch) -> None:
    """配置项从环境变量读取（不硬编码）。"""
    monkeypatch.setenv("LLM_API_KEY", "sk-test-123")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("APP_PROFILE", "prod")
    monkeypatch.setenv("APP_PORT", "9000")

    s = Settings(_env_file=None)
    assert s.llm_api_key == "sk-test-123"
    assert s.llm_model == "deepseek-v4-pro"
    assert s.app_profile == Profile.PROD
    assert s.app_port == 9000


def test_invalid_profile_rejected() -> None:
    """非法 APP_PROFILE 被校验拒绝。"""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_profile="staging")
