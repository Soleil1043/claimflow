"""诊断 ICD-10 匹配工具（T016，F09）。

将诊断描述与 ICD-10 编码匹配，判断是否在保障范围内；
结合就诊日期与保单生效日期计算等待期状态（医疗险疾病 30 天）。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import Field

from schemas.tools import ToolInput, ToolOutput
from tools.base import BaseTool

# 保障范围对照表（与 data/kb_docs/10-ICD10与保障范围对照.md 一致，规则内置常量）
_ICD10_COVERAGE: dict[str, dict[str, Any]] = {
    "K35": {"name": "急性阑尾炎", "covered": True, "scope": "住院医疗责任范围，手术与住院费用可赔"},
    "K29.3": {"name": "慢性浅表性胃炎", "covered": True, "scope": "住院治疗可赔，常规门诊不在医疗险范围"},
    "K25": {"name": "胃溃疡", "covered": True, "scope": "住院治疗可赔"},
    "K80": {"name": "胆石症", "covered": True, "scope": "住院手术治疗可赔"},
    "J18": {"name": "肺炎", "covered": True, "scope": "住院治疗可赔"},
    "J45": {"name": "支气管哮喘", "covered": True, "scope": "急性发作住院可赔"},
    "J20.9": {"name": "急性支气管炎", "covered": True, "scope": "住院治疗可赔，门诊一般不在范围"},
    "N20.0": {"name": "肾结石", "covered": True, "scope": "住院手术可赔"},
    "N18": {"name": "慢性肾脏病", "covered": True, "scope": "透析属特殊门诊责任，可赔"},
    "I10": {"name": "原发性高血压", "covered": True, "scope": "并发症住院可赔，常规门诊慢性病管理不在医疗险范围"},
    "I21": {"name": "急性心肌梗死", "covered": True, "scope": "重疾责任+住院费用均可赔"},
    "C00": {"name": "恶性肿瘤（泛编码）", "covered": True, "scope": "重疾确诊即赔（需组织病理学确诊）+医疗费用可赔"},
    "S72": {"name": "股骨骨折", "covered": True, "scope": "意外导致则意外险+医疗险均可赔"},
}

# 常见诊断描述 → ICD-10 关键词映射（按匹配优先级）
_DIAGNOSIS_KEYWORDS: list[tuple[str, str]] = [
    ("阑尾炎", "K35"),
    ("胃炎", "K29.3"),
    ("胃溃疡", "K25"),
    ("胆石", "K80"),
    ("胆囊结石", "K80"),
    ("肺炎", "J18"),
    ("哮喘", "J45"),
    ("支气管炎", "J20.9"),
    ("肾结石", "N20.0"),
    ("肾脏病", "N18"),
    ("肾炎", "N18"),
    ("高血压", "I10"),
    ("心肌梗死", "I21"),
    ("心梗", "I21"),
    ("骨折", "S72"),
    ("癌", "C00"),
    ("恶性肿瘤", "C00"),
    ("肿瘤", "C00"),
]

# 医疗险疾病等待期（天）
WAITING_PERIOD_DAYS = 30


class DiagnosisMatcherInput(ToolInput):
    """诊断匹配入参。"""

    diagnosis_desc: str = Field(description="诊断描述，如'急性阑尾炎'")
    # 可选上下文：用于等待期判断
    visit_date: str | None = Field(default=None, description="就诊日期 YYYY-MM-DD")
    policy_effective_date: str | None = Field(default=None, description="保单生效日期 YYYY-MM-DD")


class DiagnosisMatcherOutput(ToolOutput):
    """匹配输出：data 含 icd10 / 保障范围 / 等待期状态。"""


class DiagnosisMatcherTool(BaseTool[DiagnosisMatcherInput, DiagnosisMatcherOutput]):
    name = "diagnosis_matcher"
    description = (
        "将诊断描述与 ICD-10 编码匹配并判断是否在保障范围内；"
        "提供就诊日期与保单生效日期时同步计算等待期状态（医疗险疾病等待期 30 天）。"
        "用户描述病情/诊断、询问是否在保障范围、判断能否理赔时使用。"
    )
    input_schema = DiagnosisMatcherInput
    output_schema = DiagnosisMatcherOutput

    async def _run(self, input_data: DiagnosisMatcherInput) -> DiagnosisMatcherOutput:
        desc = input_data.diagnosis_desc.strip()

        # 1. ICD-10 匹配：显式编码优先，其次关键词
        icd10 = self._match_icd10(desc)
        if icd10 is None:
            return DiagnosisMatcherOutput(
                success=True,
                data={
                    "icd10_code": None,
                    "diagnosis_desc": desc,
                    "covered": None,
                    "coverage_note": "未匹配到已知 ICD-10 编码，需人工核对诊断",
                    "waiting_period": None,
                },
            )

        coverage = _ICD10_COVERAGE[icd10]

        # 2. 等待期计算（两个日期都提供时）
        waiting = self._check_waiting_period(input_data)

        return DiagnosisMatcherOutput(
            success=True,
            data={
                "icd10_code": icd10,
                "diagnosis_name": coverage["name"],
                "diagnosis_desc": desc,
                "covered": coverage["covered"],
                "coverage_note": coverage["scope"],
                "waiting_period": waiting,
            },
        )

    @staticmethod
    def _match_icd10(desc: str) -> str | None:
        """诊断描述 → ICD-10 编码（显式编码 > 关键词）。"""
        # 描述中已含 ICD-10 编码（如"急性阑尾炎 K35"）
        import re

        explicit = re.search(r"\b([A-Z]\d{2}(?:\.\d+)?)\b", desc)
        if explicit and explicit.group(1) in _ICD10_COVERAGE:
            return explicit.group(1)

        for keyword, code in _DIAGNOSIS_KEYWORDS:
            if keyword in desc:
                return code
        return None

    @staticmethod
    def _check_waiting_period(input_data: DiagnosisMatcherInput) -> dict[str, Any] | None:
        """等待期状态：就诊日期距保单生效日不足 30 天 → in_waiting_period=True。"""
        if not input_data.visit_date or not input_data.policy_effective_date:
            return None
        try:
            visit = dt.date.fromisoformat(input_data.visit_date)
            effective = dt.date.fromisoformat(input_data.policy_effective_date)
        except ValueError:
            return None

        days = (visit - effective).days
        in_waiting = days < WAITING_PERIOD_DAYS
        return {
            "days_since_effective": days,
            "waiting_period_days": WAITING_PERIOD_DAYS,
            "in_waiting_period": in_waiting,
            "note": (
                f"就诊距保单生效仅 {days} 天，处于 {WAITING_PERIOD_DAYS} 天等待期内，疾病医疗不承担赔付"
                if in_waiting
                else f"就诊距保单生效 {days} 天，已过 {WAITING_PERIOD_DAYS} 天等待期"
            ),
        }
