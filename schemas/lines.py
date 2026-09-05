"""险种 pack 注册表（T096，评审候选 2 / D039"险种 pack 分批上线"的 seam）。

每个险种一份 InsuranceLinePack：受理分类（产品类型归属）、材料完整性清单、
合法材料类型、条款要素、责任认定兜底规则（除外关键词/自费识别）。全部声明式数据——
上线新险种 = 新增一个 pack 实例 + skills/<stage>/<line>.md 规程文本，节点零改动。

消费方：intake（分类/上线判定）、completeness（必需材料）、policy_query（条款要素）、
liability_judge（兜底规则/Agent 装配线别）、cases API（doc_type 白名单）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from schemas.contract import WAITING_PERIOD_DAYS


@dataclass(frozen=True)
class InsuranceLinePack:
    """单个险种的全部声明式知识。

    Attributes:
        line: 险种枚举值（schemas.stages.InsuranceLine）
        product_types: 归入本险种的保单产品类型（intake 按 product_type 分类）
        online: worker pack 是否已上线（未上线险种受理期转人工，D039）
        required_docs: 材料完整性必需清单（(doc_type, 展示名)，顺序即缺失提示序）
        doc_types: 本险种合法材料类型（API 上传白名单口径）
        policy_terms: 条款要素（等待期天数/除外/保障范围/限额说明），空 dict = 未配置
        exclusion_keywords: 责任认定关键词兜底 ((关键词, 除外项名称)，顺序即匹配优先级)
        self_pay_pattern: 自费/乙类自付金额识别正则（partial 判定），None = 不启用
    """

    line: str
    product_types: tuple[str, ...] = ()
    online: bool = False
    required_docs: tuple[tuple[str, str], ...] = ()
    doc_types: tuple[str, ...] = ()
    policy_terms: dict[str, Any] = field(default_factory=dict)
    exclusion_keywords: tuple[tuple[str, str], ...] = ()
    self_pay_pattern: str | None = None


# ===== 首批：医疗险 pack（D039）=====
MEDICAL_PACK = InsuranceLinePack(
    line="medical",
    product_types=("医疗险",),
    online=True,
    required_docs=(
        ("invoice", "医疗发票"),
        ("diagnosis", "诊断证明"),
        ("cost_list", "费用清单"),
    ),
    doc_types=("invoice", "diagnosis", "cost_list", "medical_record"),
    policy_terms={
        "waiting_period_days": WAITING_PERIOD_DAYS,
        "exclusions": ["整形美容", "牙科", "矫正", "先天性疾病", "既往症"],
        "coverage_scope": ["疾病住院医疗", "住院手术"],
        "limit_notes": "累计赔付不超过保额",
    },
    # 除外责任关键词 → 除外项名称（兜底规则 + 前置判定不可用时的最后防线）
    exclusion_keywords=(
        ("整形", "整形美容"),
        ("美容", "整形美容"),
        ("种植牙", "牙科"),
        ("正畸", "牙科"),
        ("牙科", "牙科"),
        ("矫正", "矫正"),
    ),
    # 自费/乙类自付金额识别（如"自费内固定材料2500元"）
    self_pay_pattern=r"(?:自费|自付)[^0-9]{0,12}([0-9]+(?:\.[0-9]+)?)\s*元",
)

# ===== 二期占位：未上线险种（受理期即转人工；条款/材料单随 pack 立项补充）=====
AUTO_PACK = InsuranceLinePack(line="auto", product_types=("车险",))
PROPERTY_PACK = InsuranceLinePack(line="property", product_types=("财产险",))
ACCIDENT_PACK = InsuranceLinePack(line="accident", product_types=("意外险",))

LINE_PACKS: dict[str, InsuranceLinePack] = {
    p.line: p for p in (MEDICAL_PACK, AUTO_PACK, PROPERTY_PACK, ACCIDENT_PACK)
}

_PACKS_BY_PRODUCT_TYPE: dict[str, InsuranceLinePack] = {
    pt: p for p in LINE_PACKS.values() for pt in p.product_types
}


def get_line_pack(line: str | None) -> InsuranceLinePack | None:
    """按险种枚举值取 pack；未知险种（unknown 等）返回 None。"""
    return LINE_PACKS.get(line) if line else None


def pack_for_product_type(product_type: str) -> InsuranceLinePack | None:
    """按保单产品类型取归属 pack（intake 分类口径）；未识别返回 None。"""
    return _PACKS_BY_PRODUCT_TYPE.get(product_type)


def online_lines() -> frozenset[str]:
    """已上线 worker pack 的险种（route_after_intake 派发口径）。"""
    return frozenset(p.line for p in LINE_PACKS.values() if p.online)


def all_doc_types() -> frozenset[str]:
    """全部险种的合法材料类型并集（API 上传白名单口径）。"""
    return frozenset(dt for p in LINE_PACKS.values() for dt in p.doc_types)
