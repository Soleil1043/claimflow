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
        liability_tools: 责任认定 Agent 工具集（医疗线加诊断匹配，其余仅条款检索）
    """

    line: str
    product_types: tuple[str, ...] = ()
    online: bool = False
    required_docs: tuple[tuple[str, str], ...] = ()
    doc_types: tuple[str, ...] = ()
    policy_terms: dict[str, Any] = field(default_factory=dict)
    exclusion_keywords: tuple[tuple[str, str], ...] = ()
    self_pay_pattern: str | None = None
    liability_tools: tuple[str, ...] = ("claim_rule_rag",)


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
        ("隆鼻", "整形美容"),  # 同义词变体（T142：对抗集 ADV-101）
        ("美白", "牙科"),  # 牙齿/皮肤美白属美容修复（T101 补：金样本 E-0056）
        ("种植牙", "牙科"),
        ("牙齿种植", "牙科"),  # 语序变体（T101 补：金样本 E-0062）
        ("正畸", "牙科"),
        ("矫治", "牙科"),  # "牙齿矫治"语序变体（T142：对抗集 ADV-102）
        ("牙科", "牙科"),
        ("矫正", "矫正"),
        ("摘镜", "矫正"),  # "近视激光摘镜手术"=视力矫正（T142：对抗集 ADV-103）
    ),
    # 自费/乙类自付金额识别（如"自费内固定材料2500元"）
    self_pay_pattern=r"(?:自费|自付)[^0-9]{0,12}([0-9]+(?:\.[0-9]+)?)\s*元",
    # 责任认定工具：条款检索 + 诊断匹配（医疗线专用）
    liability_tools=("claim_rule_rag", "diagnosis_matcher"),
)

# ===== 二批上线：车险 / 财产险 / 意外险（T120，D053）=====
AUTO_PACK = InsuranceLinePack(
    line="auto",
    product_types=("车险",),
    online=True,
    required_docs=(
        ("police_report", "交通事故认定书"),
        ("repair_invoice", "维修发票"),
        ("loss_assessment", "维修定损单"),
    ),
    doc_types=("police_report", "repair_invoice", "loss_assessment", "driving_license"),
    policy_terms={
        "waiting_period_days": 0,  # 车险无等待期，保险期间内出险即受理
        "exclusions": ["酒后驾驶", "无证驾驶", "肇事逃逸", "竞赛或测试期间", "故意行为"],
        "coverage_scope": ["碰撞事故车损", "自然灾害车损"],
        "limit_notes": "单车损失累计不超过保额；绝对免赔额按条款约定扣除",
    },
    exclusion_keywords=(
        ("酒驾", "酒后驾驶"),
        ("酒后驾驶", "酒后驾驶"),
        ("醉酒驾驶", "酒后驾驶"),
        ("酒开车", "酒后驾驶"),  # "喝了点酒开车"口语变体（T142：对抗集 ADV-104）
        ("无证驾驶", "无证驾驶"),
        ("肇事逃逸", "肇事逃逸"),
        ("逃逸", "肇事逃逸"),
        ("赛车", "竞赛或测试期间"),
        ("测试车辆", "竞赛或测试期间"),
        ("故意撞击", "故意行为"),
    ),
)

PROPERTY_PACK = InsuranceLinePack(
    line="property",
    product_types=("财产险",),
    online=True,
    required_docs=(
        ("incident_proof", "事故证明（警方/消防）"),
        ("loss_list", "损失清单"),
        ("purchase_receipt", "购置凭证"),
    ),
    doc_types=("incident_proof", "loss_list", "purchase_receipt", "property_photo"),
    policy_terms={
        "waiting_period_days": 0,
        "exclusions": ["地震海啸", "战争军事行为", "金银珠宝及有价证券", "故意行为", "自然磨损"],
        "coverage_scope": ["火灾爆炸", "暴雨台风等自然灾害", "外来盗抢"],
        "limit_notes": "房屋主体与室内财产分项限额，累计不超过保额",
    },
    exclusion_keywords=(
        ("地震", "地震海啸"),
        ("海啸", "地震海啸"),
        ("战争", "战争军事行为"),
        ("军事演习", "战争军事行为"),
        ("金银", "金银珠宝及有价证券"),
        ("珠宝", "金银珠宝及有价证券"),
        ("玉器", "金银珠宝及有价证券"),  # 贵重物品同义（T142：对抗集 ADV-106）
        ("首饰", "金银珠宝及有价证券"),
        ("现金", "金银珠宝及有价证券"),
        ("有价证券", "金银珠宝及有价证券"),
        ("故意纵火", "故意行为"),
        ("自然磨损", "自然磨损"),
    ),
)

ACCIDENT_PACK = InsuranceLinePack(
    line="accident",
    product_types=("意外险",),
    online=True,
    required_docs=(
        ("incident_proof", "事故证明"),
        ("diagnosis", "诊断证明"),
        ("invoice", "医疗费用发票"),
    ),
    doc_types=("incident_proof", "diagnosis", "invoice", "medical_record"),
    policy_terms={
        "waiting_period_days": 0,  # 意外险无等待期（区别于医疗险疾病等待期）
        "exclusions": ["高风险运动", "酒后意外", "自伤自残", "无证驾驶", "战争军事行为"],
        "coverage_scope": ["意外伤害医疗", "意外伤残", "意外身故"],
        "limit_notes": "意外医疗累计不超过意外医疗保额",
    },
    exclusion_keywords=(
        ("潜水", "高风险运动"),
        ("深潜", "高风险运动"),  # "深潜活动"=潜水变体（T142：对抗集 ADV-105）
        ("攀岩", "高风险运动"),
        ("跳伞", "高风险运动"),
        ("蹦极", "高风险运动"),
        ("酒后", "酒后意外"),
        ("醉酒", "酒后意外"),
        ("自残", "自伤自残"),
        ("自杀", "自伤自残"),
        ("酒驾", "酒后意外"),
        ("无证驾驶", "无证驾驶"),
    ),
    # 意外医疗同样存在自费药/乙类自付（与医疗险同口径识别）
    self_pay_pattern=r"(?:自费|自付)[^0-9]{0,12}([0-9]+(?:\.[0-9]+)?)\s*元",
)

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


# 险种展示名（前端材料目录分组标签，T126）
LINE_LABELS: dict[str, str] = {
    "medical": "医疗险",
    "auto": "车险",
    "property": "财产险",
    "accident": "意外险",
}


def material_catalog() -> list[dict[str, Any]]:
    """材料类型目录（按险种分组，含展示名）：前端下拉/补件表单的唯一来源（T126）。

    只列有展示名的必备材料类型；新险种上线目录自动扩展，前端零改动。
    """
    return [
        {
            "line": pack.line,
            "label": LINE_LABELS.get(pack.line, pack.line),
            "docs": [{"value": code, "label": label} for code, label in pack.required_docs],
        }
        for pack in LINE_PACKS.values()
        if pack.online and pack.required_docs
    ]
