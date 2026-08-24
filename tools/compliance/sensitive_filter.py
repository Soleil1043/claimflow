"""敏感信息脱敏工具（T019，F11）。

对文本中的敏感信息脱敏：
- 18 位身份证号：保留前 4 后 4 位 → 3301**********1234
- 银行卡号（16-19 位）：保留前 4 后 4 位 → 6222**********3445
- 手机号（11 位）：保留前 3 后 4 位 → 138****5678

正则模式与 rule_check 的 PRIVACY 检测共用（单一来源）；
替换顺序：身份证 → 银行卡 → 手机号（先长的替换后，脱敏后的星号段
不会再次命中后续正则，天然去重）。

纯函数 mask_sensitive / find_sensitive 供合规链路直接调用；
BaseTool 封装注册后供 Compliance Agent（T015 定义）使用。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from schemas.tools import ToolInput, ToolOutput
from tools.base import BaseTool
from tools.compliance.rule_check import _BANK_CARD_RE, _ID_CARD_RE, _PHONE_RE


def _mask_id_card(match: Any) -> str:
    snippet = match.group(0)
    return snippet[:4] + "*" * (len(snippet) - 8) + snippet[-4:]


def _mask_bank_card(match: Any) -> str:
    snippet = match.group(0)
    return snippet[:4] + "*" * (len(snippet) - 8) + snippet[-4:]


def _mask_phone(match: Any) -> str:
    snippet = match.group(0)
    return snippet[:3] + "*" * (len(snippet) - 7) + snippet[-4:]


def find_sensitive(text: str) -> list[dict[str, str]]:
    """检测文本中的敏感信息（不修改原文）。

    返回 [{type, value, masked}]；身份证与银行卡片段去重
    （银行卡正则不匹配已被身份证占位的片段）。
    """
    findings: list[dict[str, str]] = []
    id_spans = [(m.start(), m.end()) for m in _ID_CARD_RE.finditer(text)]
    for match in _ID_CARD_RE.finditer(text):
        findings.append(
            {"type": "id_card", "value": match.group(0), "masked": _mask_id_card(match)}
        )
    for match in _BANK_CARD_RE.finditer(text):
        if not any(match.start() < e and match.end() > s for s, e in id_spans):
            findings.append(
                {"type": "bank_card", "value": match.group(0), "masked": _mask_bank_card(match)}
            )
    for match in _PHONE_RE.finditer(text):
        findings.append(
            {"type": "phone", "value": match.group(0), "masked": _mask_phone(match)}
        )
    return findings


def mask_sensitive(text: str) -> str:
    """脱敏文本中的全部敏感信息（身份证 → 银行卡 → 手机号，顺序替换）。"""
    masked = _ID_CARD_RE.sub(_mask_id_card, text)
    masked = _BANK_CARD_RE.sub(_mask_bank_card, masked)
    masked = _PHONE_RE.sub(_mask_phone, masked)
    return masked


class SensitiveFilterInput(ToolInput):
    """脱敏入参。"""

    text: str = Field(description="待脱敏的文本", min_length=1)


class SensitiveFilterOutput(ToolOutput):
    """脱敏输出：data 含 masked_text / findings / masked_count。"""


class SensitiveFilterTool(BaseTool[SensitiveFilterInput, SensitiveFilterOutput]):
    name = "sensitive_filter"
    description = (
        "对文本中的敏感信息脱敏：18 位身份证号保留前 4 后 4 位（如 3301**********1234）、"
        "银行卡号保留前 4 后 4 位、手机号保留前 3 后 4 位。"
        "回答中包含用户证件号/卡号/手机号需要脱敏时使用。"
    )
    input_schema = SensitiveFilterInput
    output_schema = SensitiveFilterOutput

    async def _run(self, input_data: SensitiveFilterInput) -> SensitiveFilterOutput:
        findings = find_sensitive(input_data.text)
        masked = mask_sensitive(input_data.text)
        return SensitiveFilterOutput(
            success=True,
            data={
                "masked_text": masked,
                "findings": findings,
                "masked_count": len(findings),
            },
        )
