"""合规规则检查工具（T018，F10）。

对拟返回给用户的回答草稿做正则规则检测，识别五类违规：
PROMISE（承诺性话术）/ ABSOLUTE（绝对化用语）/ MISLEAD（误导性表述）/
FRAUD_RISK（欺诈风险）/ PRIVACY（未脱敏敏感信息）。

工具层同时提供纯函数 check_text（供合规节点在工具不可用时兜底，
保证拦截能力恒在，见 decisions.md D012）。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field

from schemas.tools import ToolInput, ToolOutput
from tools.base import BaseTool

# 违规类型 → (正则, 修改建议)
_TEXT_PATTERNS: list[tuple[str, str, str]] = [
    (
        "PROMISE",
        r"保证赔付|保证赔偿|保证理赔|一定能赔|肯定能赔|肯定赔|"
        r"百分之百(?:赔付|报销|赔偿)|100%(?:赔付|报销|赔偿)|包赔|稳赔",
        "将承诺性话术改为预估表述，如「根据条款预估可赔付金额，最终以理赔审核结果为准」",
    ),
    (
        "ABSOLUTE",
        r"绝对(?:安全|没有风险|没问题|会赔|能赔|报销)|必然(?:会赔|能赔|赔付|通过)|"
        r"百分之百|绝对能",
        "删除绝对化用语，改为客观中性表述（保险赔付以条款与审核结果为准，不存在绝对结论）",
    ),
    (
        "MISLEAD",
        r"无需审核|免审核|不用审核|无需材料|不需要(?:任何)?材料|"
        r"什么都能赔|所有费用都(?:能|可以)(?:赔|报)|肯定通过|隐瞒免责",
        "明确区分预估与确定结论，补充「最终以理赔审核结果为准」及免责条款提示",
    ),
    (
        "FRAUD_RISK",
        r"代开发票|虚开(?:发票|票据)|挂床住院|冒名(?:顶替|就诊|理赔)|"
        r"伪造(?:病历|发票|诊断|证明|印章)|骗保|骗取保金",
        "删除涉嫌保险欺诈的表述，此类内容不得以任何形式返回用户，建议转人工处理",
    ),
]

# PRIVACY：身份证（18 位）/ 手机号（11 位）/ 银行卡（16-19 位）
_ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_BANK_CARD_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")

_PRIVACY_SUGGESTION = "敏感信息须脱敏后输出（身份证号保留前 4 后 4 位，如 3301**********1234）"


def _mask(snippet: str) -> str:
    """证据片段脱敏展示（工具输出本身不得泄露完整敏感号）。"""
    if len(snippet) <= 8:
        return snippet[0] + "*" * (len(snippet) - 2) + snippet[-1]
    return snippet[:4] + "*" * (len(snippet) - 8) + snippet[-4:]


def check_text(text: str) -> list[dict[str, Any]]:
    """纯函数：检测文本中的违规表述，返回违规项列表 [{type, detail, suggestion}]。"""
    violations: list[dict[str, Any]] = []
    for vtype, pattern, suggestion in _TEXT_PATTERNS:
        for match in re.finditer(pattern, text):
            violations.append(
                {"type": vtype, "detail": match.group(0), "suggestion": suggestion}
            )

    # PRIVACY：先身份证，银行卡排除与身份证重叠的片段，手机号独立（11 位不与 16-19 位冲突）
    id_spans = [(m.start(), m.end()) for m in _ID_CARD_RE.finditer(text)]
    for start, end in id_spans:
        violations.append(
            {"type": "PRIVACY", "detail": _mask(text[start:end]), "suggestion": _PRIVACY_SUGGESTION}
        )
    for match in _BANK_CARD_RE.finditer(text):
        if not any(match.start() < e and match.end() > s for s, e in id_spans):
            violations.append(
                {"type": "PRIVACY", "detail": _mask(match.group(0)), "suggestion": _PRIVACY_SUGGESTION}
            )
    for match in _PHONE_RE.finditer(text):
        violations.append(
            {"type": "PRIVACY", "detail": _mask(match.group(0)), "suggestion": _PRIVACY_SUGGESTION}
        )
    return violations


class RuleCheckInput(ToolInput):
    """规则检查入参。"""

    text: str = Field(description="待检查的回答文本", min_length=1)


class RuleCheckOutput(ToolOutput):
    """检查输出：data 含 violations / violation_count / violation_types。"""


class ComplianceRuleCheckTool(BaseTool[RuleCheckInput, RuleCheckOutput]):
    name = "compliance_rule_check"
    description = (
        "对拟返回给用户的回答做合规规则检查（正则）：识别承诺性话术（保证赔付等）、"
        "绝对化用语、误导性表述、保险欺诈风险表述、未脱敏的身份证/银行卡/手机号。"
        "合规审查回答是否违规时使用。"
    )
    input_schema = RuleCheckInput
    output_schema = RuleCheckOutput

    async def _run(self, input_data: RuleCheckInput) -> RuleCheckOutput:
        violations = check_text(input_data.text)
        return RuleCheckOutput(
            success=True,
            data={
                "violations": violations,
                "violation_count": len(violations),
                "violation_types": sorted({v["type"] for v in violations}),
            },
        )
