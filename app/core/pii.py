"""PII 识别与脱敏（单一来源，T175/D081）。

正则与脱敏函数从 tools/compliance/{rule_check, sensitive_filter} 收敛至此——
app.core.logging 的日志零 PII 处理器（T175）与合规工具链共用同一套模式，
避免两处正则漂移（本模块零项目内依赖，日志配置期 import 无环）。

脱敏口径：
- 18 位身份证号：保留前 4 后 4 位 → 3301**********1234
- 银行卡号（16-19 位）：保留前 4 后 4 位 → 6222**********3445
- 手机号（11 位）：保留前 3 后 4 位 → 138****5678

替换顺序：身份证 → 银行卡 → 手机号（先长的替换后，脱敏后的星号段
不会再次命中后续正则，天然去重）。
"""

from __future__ import annotations

import re
from typing import Any

# 边界断言 (?<!\d)…(?!\d)：嵌在更长数字串中的片段不命中（身份证 18 位优先于银行卡）
ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
BANK_CARD_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")


def _mask_keep_ends(match: re.Match[str], head: int, tail: int) -> str:
    snippet = match.group(0)
    return snippet[:head] + "*" * (len(snippet) - head - tail) + snippet[-tail:]


def _mask_id_card(match: Any) -> str:
    return _mask_keep_ends(match, head=4, tail=4)


def _mask_bank_card(match: Any) -> str:
    return _mask_keep_ends(match, head=4, tail=4)


def _mask_phone(match: Any) -> str:
    return _mask_keep_ends(match, head=3, tail=4)


def find_sensitive(text: str) -> list[dict[str, str]]:
    """检测文本中的敏感信息（不修改原文）。

    返回 [{type, value, masked}]；身份证与银行卡片段去重
    （银行卡正则不匹配已被身份证占位的片段）。
    """
    findings: list[dict[str, str]] = []
    id_spans = [(m.start(), m.end()) for m in ID_CARD_RE.finditer(text)]
    for match in ID_CARD_RE.finditer(text):
        findings.append(
            {"type": "id_card", "value": match.group(0), "masked": _mask_id_card(match)}
        )
    for match in BANK_CARD_RE.finditer(text):
        if not any(match.start() < e and match.end() > s for s, e in id_spans):
            findings.append(
                {"type": "bank_card", "value": match.group(0), "masked": _mask_bank_card(match)}
            )
    for match in PHONE_RE.finditer(text):
        findings.append(
            {"type": "phone", "value": match.group(0), "masked": _mask_phone(match)}
        )
    return findings


def mask_sensitive(text: str) -> str:
    """脱敏文本中的全部敏感信息（身份证 → 银行卡 → 手机号，顺序替换）。"""
    masked = ID_CARD_RE.sub(_mask_id_card, text)
    masked = BANK_CARD_RE.sub(_mask_bank_card, masked)
    masked = PHONE_RE.sub(_mask_phone, masked)
    return masked


def mask_value(value: Any) -> Any:
    """递归脱敏任意日志字段值（str / dict / list / tuple；其余类型原样返回）。

    供 structlog 处理器使用（T175 日志零 PII）：异常串（format_exc_info 的
    渲染产物）与嵌套结构（state dump / payload）同样过一层。
    """
    if isinstance(value, str):
        return mask_sensitive(value)
    if isinstance(value, dict):
        return {k: mask_value(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return tuple(mask_value(v) for v in value)
    if isinstance(value, list):
        return [mask_value(v) for v in value]
    return value
