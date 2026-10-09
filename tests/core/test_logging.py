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
    # prod 校验要求坐席鉴权非空（T147）
    monkeypatch.setenv("STAFF_KEYS", "smoke:k1")
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


# ===== T175 日志零 PII =====


def test_pii_mask_processor_masks_nested_fields() -> None:
    """处理器递归脱敏：str / dict / list 内的身份证/银行卡/手机号全 masked。"""
    from app.core.logging import pii_mask_processor

    event = {
        "event": "case_submit",
        "description": "持有人 330106199203154817，电话 13812345678",
        "payload": {"id_card": "330106199203154817", "cards": ["6222000011112222"]},
        "count": 3,
        "ok": True,
    }
    out = pii_mask_processor(None, "info", dict(event))
    dumped = str(out)
    assert "330106199203154817" not in dumped
    assert "13812345678" not in dumped
    assert "6222000011112222" not in dumped
    assert out["payload"]["id_card"] == "3301**********4817"
    # 非字符串字段原样透传（不误伤数字/布尔）
    assert out["count"] == 3 and out["ok"] is True


def test_pii_mask_processor_masks_exception_string() -> None:
    """异常渲染串（format_exc_info 产物）同样过一层——错误消息常带原始入参。"""
    from app.core.logging import pii_mask_processor

    event = {"event": "x", "exception": "ValueError: bad id 330106199203154817"}
    out = pii_mask_processor(None, "error", event)
    assert "330106199203154817" not in out["exception"]
    assert "3301**********4817" in out["exception"]


def test_json_log_masks_pii_end_to_end(monkeypatch, capsys) -> None:
    """e2e：prod JSON 日志输出的任何字段不出现完整敏感号（T175 验收口径）。"""
    monkeypatch.setenv("APP_PROFILE", "prod")
    monkeypatch.setenv("STAFF_KEYS", "smoke:k1")
    import importlib

    import app.core.config as config_module
    importlib.reload(config_module)
    import app.core.logging as logging_module
    importlib.reload(logging_module)

    logging_module.configure_logging()
    log = logging_module.get_logger("pii-e2e-test")
    log.info(
        "policy_loaded",
        holder_id_card="330106199203154817",
        snapshot={"phone": "13812345678"},
    )

    err = capsys.readouterr().err
    assert "330106199203154817" not in err
    assert "13812345678" not in err
    assert "3301**********4817" in err

    # 恢复全局单例状态，避免影响其他测试
    importlib.reload(config_module)
    importlib.reload(logging_module)
