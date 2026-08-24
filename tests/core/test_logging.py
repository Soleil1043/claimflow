"""app.core.logging 单元测试。"""

import json
import logging

import structlog

from app.core.logging import configure_logging, get_logger


def test_configure_logging_sets_root_level() -> None:
    """初始化后 root logger 使用配置的日志级别。"""
    configure_logging()
    assert logging.getLogger().level == logging.INFO


def test_get_logger_emits_structured_json_in_prod(monkeypatch, capsys) -> None:
    """prod profile 下日志输出为 JSON 单行，且结构化字段保留。"""
    monkeypatch.setenv("APP_PROFILE", "prod")
    # 重新初始化使新 profile 生效
    import importlib

    import app.core.config as config_module
    importlib.reload(config_module)
    import app.core.logging as logging_module
    importlib.reload(logging_module)

    logging_module.configure_logging()
    log = logging_module.get_logger("test")
    log.info("tool_executed", tool="policy_query", duration_ms=120)

    # logging.StreamHandler 默认写 stderr
    err = capsys.readouterr().err
    parsed = json.loads(err.strip().splitlines()[-1])
    assert parsed["event"] == "tool_executed"
    assert parsed["tool"] == "policy_query"
    assert parsed["duration_ms"] == 120
    assert parsed["level"] == "info"

    # 恢复全局单例状态，避免影响其他测试
    importlib.reload(config_module)
    importlib.reload(logging_module)


def test_get_logger_returns_structlog_bound_logger() -> None:
    """get_logger 返回 structlog 绑定 logger（bind 后为 BoundLogger 实例）。"""
    log = get_logger("test").bind()
    assert isinstance(log, structlog.stdlib.BoundLogger)
