"""T088 核赔金样本评测集生成器（确定性枚举，无随机）。

数据源：
- 24 个手工底座案件（data/mock/cases.json，CASE-2026-0001..0024 原样收录）
- 保单条款参数（data/mock/policies.json：免赔/比例/等待期 30 天→期望按规格公式计算）
- 黑名单与频率信号（tools/fraud 的 mock 口径：赵敏黑名单、孙强近 90 天 2 次）

生成枚举（CASE-E-#### ）：
- 正常自动签发：金额阶梯（免赔临界/中段/逼近签发线）× 3 张医疗险保单
- 超阈值转人工：金额 > 自动签发线（核定 > 5000）
- 等待期边界：生效日 + {10,20,29,30,31,45} 天（≤30 拒、≥31 过）
- 除外责任：整形/美容/牙科/种植牙/矫正/正畸
- 部分责任：自费金额阶梯（先扣自费再扣免赔乘比例）
- 欺诈：黑名单人案件（高风险短路）+ 频率（frequency_signals 相对天数）
- 边界：免赔临界 0 元、保额封顶、材料矛盾、重复申请
- 三线组（T120）：车险/财产险/意外险各自的正常/超阈值/除外/缺件/退保拒赔
- 缺件：三类单一缺失 + 组合缺失
- 未分类边界：产品类型不在任何 pack（unknown）受理转人工

金额公式（规格）：approved = min(max(claimed − selfpay − deductible, 0) × ratio, coverage)；
rejection（等待期/除外/过期）→ 0.00。期望由本脚本按规格计算（规格即实现依据，
两者同源；实现正确性由 T079/T083 单测与 T089 运行时校验兜底）。

用法：uv run python -m scripts.gen_adjudication_cases
输出：evals/datasets/adjudication.json（幂等覆盖）
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

from schemas import contract
from schemas.lines import ACCIDENT_PACK, AUTO_PACK, MEDICAL_PACK, PROPERTY_PACK
from services.amounts import approved_amount

ROOT = Path(__file__).resolve().parent.parent
MOCK_DIR = ROOT / "data" / "mock"
OUT_PATH = ROOT / "evals" / "datasets" / "adjudication.json"

# 规格常量与金额公式引用契约模块（T097）——运行时/生成器/评测门同一数值来源
AUTO_APPROVE_LIMIT = contract.AUTO_APPROVE_LIMIT
WAITING_DAYS = contract.WAITING_PERIOD_DAYS


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _approved(claimed: Decimal, policy: dict, selfpay: Decimal = Decimal("0")) -> Decimal:
    """规格公式（services.amounts.approved_amount 同源，T097）。"""
    return approved_amount(
        claimed,
        selfpay,
        Decimal(policy["deductible"]),
        Decimal(policy["payout_ratio"]),
        Decimal(policy["coverage_amount"]),
    )


def main() -> None:
    policies = {
        p["policy_no"]: p
        for p in json.loads((MOCK_DIR / "policies.json").read_text(encoding="utf-8"))
    }
    base_cases = json.loads((MOCK_DIR / "cases.json").read_text(encoding="utf-8"))
    cases: list[dict] = [dict(c) for c in base_cases["cases"]]

    generated: list[dict] = []

    def add(
        *,
        user_id: str,
        policy_no: str,
        claimed: str,
        incident_date: str,
        incident_description: str,
        materials: list[dict] | None,
        category: str,
        route: str,
        liability: str | None,
        approved: Decimal | None,
        sequence: list[str],
        note: str = "",
        declared: str | None = "medical",
        added_materials: list[dict] | None = None,
    ) -> None:
        idx = len(generated) + 1
        entry = {
            "case_id": f"CASE-E-{idx:04d}",
            "user_id": user_id,
            "policy_no": policy_no,
            "declared_case_type": declared,
            "claimed_amount": claimed,
            "incident_date": incident_date,
            "incident_description": incident_description,
            "materials": materials if materials is not None else [],
            "expected": {
                "category": category,
                "route": route,
                "liability": liability,
                "approved_amount": _money(approved) if approved is not None else None,
                "expected_worker_sequence": sequence,
                "note": note,
            },
        }
        if added_materials:
            entry["added_materials"] = added_materials
        generated.append(entry)

    FULL_SEQ = [
        "material_review", "policy_verify", "fraud_check",
        "liability_judge", "amount_calc", "decision_generate",
    ]
    PRE_SEQ = ["material_review", "policy_verify", "fraud_check"]

    full_docs = [
        {"file_name": "invoice.jpg", "doc_type": "invoice"},
        {"file_name": "diagnosis.jpg", "doc_type": "diagnosis"},
        {"file_name": "cost_list.pdf", "doc_type": "cost_list"},
    ]
    auto_docs = [
        {"file_name": "police_report.jpg", "doc_type": "police_report"},
        {"file_name": "repair_invoice.jpg", "doc_type": "repair_invoice"},
        {"file_name": "loss_assessment.pdf", "doc_type": "loss_assessment"},
    ]
    property_docs = [
        {"file_name": "incident_proof_fire.jpg", "doc_type": "incident_proof"},
        {"file_name": "loss_list.pdf", "doc_type": "loss_list"},
        {"file_name": "purchase_receipt.jpg", "doc_type": "purchase_receipt"},
    ]
    accident_docs = [
        {"file_name": "incident_proof_fall.jpg", "doc_type": "incident_proof"},
        {"file_name": "diagnosis.jpg", "doc_type": "diagnosis"},
        {"file_name": "invoice.jpg", "doc_type": "invoice"},
    ]

    # ---------- 正常自动签发（金额阶梯 × 3 保单） ----------
    normal_grid = [
        ("POL-2025-0001", "u-zhangwei", [
            "10050.00", "10625.00", "11250.00", "11875.00",
            "12500.00", "13125.00", "14375.00", "15625.00",
        ]),
        ("POL-2026-0005", "u-liuyang", [
            "10080.00", "10875.00", "11670.00", "12465.00",
            "13260.00", "14055.00", "14850.00", "15640.00",
        ]),
        ("POL-2025-0001", "u-zhangwei", [
            "10125.00", "11500.00", "12250.00", "13600.00",
            "14750.00", "15300.00", "15900.00", "16100.00",
        ]),
        ("POL-2026-0005", "u-liuyang", [
            "10200.00", "11400.00", "12800.00", "13900.00",
            "14400.00", "15200.00", "15900.00", "16000.00",
        ]),
    ]
    diagnoses = ["肺炎住院治疗", "急性肠胃炎住院", "胆囊结石手术", "胃炎住院治疗",
                 "阑尾炎手术", "扁桃体切除手术", "疝气手术", "痔疮手术",
                 "腰椎间盘突出手术", "甲状腺结节切除", "乳腺纤维瘤手术", "白内障手术"]
    for policy_no, user, amounts in normal_grid:
        policy = policies[policy_no]
        for i, claimed in enumerate(amounts):
            approved = _approved(Decimal(claimed), policy)
            if approved > AUTO_APPROVE_LIMIT:
                continue  # 超线的进超阈值组
            day = 40 + i * 5  # 全部已过等待期
            date = (
                dt.date.fromisoformat(policy["effective_date"]) + dt.timedelta(days=day)
            ).isoformat()
            add(
                user_id=user, policy_no=policy_no, claimed=claimed,
                incident_date=date,
                incident_description=f"{diagnoses[i % len(diagnoses)]}，费用{claimed}元。",
                materials=full_docs, category="normal", route="auto",
                liability="covered", approved=approved, sequence=FULL_SEQ,
                note="正常自动签发（金额阶梯）",
            )

    # ---------- 超阈值转人工 ----------
    for policy_no, user, claimed in [
        ("POL-2025-0001", "u-zhangwei", "40000.00"),
        ("POL-2026-0005", "u-liuyang", "35000.00"),
        ("POL-2025-0001", "u-zhangwei", "25000.00"),
        ("POL-2026-0005", "u-liuyang", "20000.00"),
    ]:
        policy = policies[policy_no]
        approved = _approved(Decimal(claimed), policy)
        date = (
            dt.date.fromisoformat(policy["effective_date"]) + dt.timedelta(days=60)
        ).isoformat()
        add(
            user_id=user, policy_no=policy_no, claimed=claimed,
            incident_date=date,
            incident_description=f"骨折内固定手术，费用{claimed}元。",
            materials=full_docs, category="normal", route="human",
            liability="covered", approved=approved, sequence=FULL_SEQ,
            note="金额超自动签发线，走完管线后签批转人工",
        )

    # ---------- 等待期边界（POL-2026-0005 生效 2026-08-01） ----------
    wait_policy = policies["POL-2026-0005"]
    eff = dt.date.fromisoformat(wait_policy["effective_date"])
    for offset in (1, 5, 10, 15, 20, 25, 29, 30):
        date = (eff + dt.timedelta(days=offset)).isoformat()
        add(
            user_id="u-liuyang", policy_no="POL-2026-0005", claimed="9000.00",
            incident_date=date,
            incident_description=f"急性阑尾炎手术，费用9000元。保单生效第 {offset} 天出险。",
            materials=full_docs, category="rejected", route="auto",
            liability="not_covered", approved=Decimal("0.00"), sequence=FULL_SEQ,
            note=f"等待期内出险（第 {offset} 天），标准拒赔",
        )
    for offset in (31, 35, 45, 60):
        date = (eff + dt.timedelta(days=offset)).isoformat()
        claimed = "13000.00"
        add(
            user_id="u-liuyang", policy_no="POL-2026-0005", claimed=claimed,
            incident_date=date,
            incident_description=f"急性阑尾炎手术，费用{claimed}元。生效第 {offset} 天出险。",
            materials=full_docs, category="normal", route="auto",
            liability="covered",
            approved=_approved(Decimal(claimed), wait_policy), sequence=FULL_SEQ,
            note="等待期已过，正常赔付",
        )

    # ---------- 跨保单等待期通过（POL-2026-0006 赵敏黑名单 → human） ----------
    for claimed in ["11500.00", "13500.00", "14200.00"]:
        add(
            user_id="u-zhaomin", policy_no="POL-2026-0006", claimed=claimed,
            incident_date="2026-07-15",
            incident_description=f"急性肠胃炎住院，费用{claimed}元。",
            materials=full_docs, category="fraud", route="human",
            liability="covered", approved=None,
            sequence=PRE_SEQ,
            note="POL-2026-0006 赵敏黑名单，高风险短路",
        )

    # ---------- 除外责任（标准拒赔） ----------
    # 覆盖性契约（T101）：每条排除描述必须含险种包的至少一个排除关键词——
    # 描述词表与包关键词从此单一来源对齐，漂移在生成时即报错而非评测时静默漏判
    exclusion_keywords = [kw for kw, _ in MEDICAL_PACK.exclusion_keywords]
    for user, policy_no, desc in [
        ("u-zhangwei", "POL-2025-0001", "鼻综合整形手术"),
        ("u-zhangwei", "POL-2025-0001", "种植牙两颗"),
        ("u-zhangwei", "POL-2025-0001", "牙齿正畸矫正"),
        ("u-liuyang", "POL-2026-0005", "眼部美容手术"),
        ("u-zhangwei", "POL-2025-0001", "牙齿美白修复"),
        ("u-liuyang", "POL-2026-0005", "脊柱侧弯矫正手术"),
        ("u-zhangwei", "POL-2025-0001", "注射美容针"),
        ("u-liuyang", "POL-2026-0005", "牙齿矫正器"),
        ("u-zhangwei", "POL-2025-0001", "面部激光美容"),
        ("u-liuyang", "POL-2026-0005", "口腔正畸治疗"),
        ("u-zhangwei", "POL-2025-0001", "牙齿种植手术"),
        ("u-liuyang", "POL-2026-0005", "视力矫正激光手术"),
    ]:
        assert any(kw in desc for kw in exclusion_keywords), (
            f"排除描述「{desc}」不含险种包任何排除关键词 {exclusion_keywords}——"
            "确定性兜底将漏判，请改写描述或补包关键词"
        )
        claimed = "12000.00"
        # POL-2026-0005 生效 2026-08-01：出险日取生效+45（过等待期，测除外关键词
        # 而非保障期外）；POL-2025-0001 用 2026-07-08（T122 修复：原统一 07-08 对
        # 0005 是保障期外，not_covered 来自 precheck，关键词路径从未被测到）
        incident = "2026-09-15" if policy_no == "POL-2026-0005" else "2026-07-08"
        add(
            user_id=user, policy_no=policy_no, claimed=claimed,
            incident_date=incident,
            incident_description=f"{desc}，费用{claimed}元。",
            materials=full_docs, category="rejected", route="auto",
            liability="not_covered", approved=Decimal("0.00"), sequence=FULL_SEQ,
            note="除外责任，标准拒赔",
        )

    # ---------- 过期/退保保单拒赔（标准拒赔） ----------
    # POL-2023-0004（意外险退保）的拒赔覆盖随 T120 意外险上线移入意外险组（材料口径同步事故三件套）
    for user, policy_no, desc in [
        ("u-wangqiang", "POL-2024-0003", "肺炎住院治疗"),
    ]:
        claimed = "12000.00"
        add(
            user_id=user, policy_no=policy_no, claimed=claimed,
            incident_date="2026-06-30",
            incident_description=f"{desc}，费用{claimed}元。",
            materials=full_docs, category="rejected", route="auto",
            liability="not_covered", approved=Decimal("0.00"), sequence=FULL_SEQ,
            note="保单过期/退保，保障终止，标准拒赔",
        )

    # ---------- 部分责任（自费金额阶梯） ----------
    for user, policy_no, claimed, selfpay in [
        ("u-zhangwei", "POL-2025-0001", "17500.00", "2500.00"),
        ("u-zhangwei", "POL-2025-0001", "16000.00", "1000.00"),
        ("u-liuyang", "POL-2026-0005", "15000.00", "2000.00"),
        ("u-liuyang", "POL-2026-0005", "18000.00", "3000.00"),
        ("u-zhangwei", "POL-2025-0001", "19000.00", "4000.00"),
        ("u-liuyang", "POL-2026-0005", "16500.00", "1500.00"),
        ("u-zhangwei", "POL-2025-0001", "18000.00", "5000.00"),
        ("u-zhangwei", "POL-2025-0001", "14500.00", "500.00"),
        ("u-liuyang", "POL-2026-0005", "17000.00", "2500.00"),
        ("u-zhangwei", "POL-2025-0001", "18500.00", "3500.00"),
        ("u-liuyang", "POL-2026-0005", "15500.00", "1200.00"),
        ("u-zhangwei", "POL-2025-0001", "16800.00", "2800.00"),
        ("u-zhangwei", "POL-2025-0001", "17800.00", "4500.00"),
        ("u-liuyang", "POL-2026-0005", "17500.00", "3800.00"),
    ]:
        policy = policies[policy_no]
        approved = _approved(Decimal(claimed), policy, Decimal(selfpay))
        # POL-2026-0005 生效 2026-08-01：出险日取生效+45（T122 修复：原统一 06-05
        # 对 0005 是保障期外，6 案 not_covered 与期望 partial 矛盾）
        incident = "2026-09-15" if policy_no == "POL-2026-0005" else "2026-06-05"
        add(
            user_id=user, policy_no=policy_no, claimed=claimed,
            incident_date=incident,
            incident_description=f"骨科手术，含自费内固定材料{selfpay}元（清单单列），总费用{claimed}元。",
            materials=full_docs, category="partial", route="auto",
            liability="partial", approved=approved, sequence=FULL_SEQ,
            note="部分责任：先扣自费再扣免赔乘比例",
        )

    # ---------- 欺诈（黑名单 time-free + 频率走 frequency_signals 相对天数） ----------
    for _i, claimed in enumerate(["15800.00", "9800.00", "22000.00", "8800.00", "11500.00", "45000.00"]):
        add(
            user_id="u-zhaomin", policy_no="POL-2026-0006", claimed=claimed,
            incident_date="2026-08-08",
            incident_description="急性阑尾炎手术申请理赔。",
            materials=full_docs, category="fraud", route="human",
            liability="covered", approved=None, sequence=PRE_SEQ,
            note="黑名单命中，高风险短路转人工",
        )
    for _i, claimed in enumerate(["12800.00", "9000.00", "13800.00", "10500.00"]):
        add(
            user_id="u-sunqiang", policy_no="POL-2026-0007", claimed=claimed,
            incident_date="2026-08-20",
            incident_description="肺炎住院治疗申请理赔，近期多次申请。",
            materials=full_docs, category="fraud", route="human",
            liability="covered", approved=None,
            sequence=[*FULL_SEQ[:5]],  # medium 走完理算、签发阶段转人工
            note="高频理赔（medium），分级签发转人工",
        )

    # ---------- 缺件（单一 + 组合） ----------
    missing_cases = [
        (["invoice"], ["诊断证明", "费用清单"]),
        (["diagnosis"], ["医疗发票"]),
        (["cost_list"], ["医疗发票", "诊断证明"]),
        ([], ["医疗发票", "诊断证明", "费用清单"]),
        (["invoice", "diagnosis"], ["费用清单"]),
        (["invoice", "cost_list"], ["诊断证明"]),
        (["diagnosis", "cost_list"], ["医疗发票"]),
        (["medical_record"], ["医疗发票", "诊断证明", "费用清单"]),
    ]
    for _i, (docs, missing) in enumerate(missing_cases):
        add(
            user_id="u-zhangwei", policy_no="POL-2025-0001", claimed="15800.00",
            incident_date="2026-08-10",
            incident_description="急性阑尾炎住院手术，费用15800元。",
            materials=docs and [
                {"file_name": f"{d}.jpg", "doc_type": d} for d in docs
            ],
            category="missing", route="supplement", liability=None, approved=None,
            sequence=["material_review"],
            note=f"缺件：{'、'.join(missing)}",
        )

    # ---------- 车险组（T120 上线，POL-2026-0008：免赔 500 / 比例 1.0 / 无等待期） ----------
    auto_kw = [kw for kw, _ in AUTO_PACK.exclusion_keywords]
    auto_policy = policies["POL-2026-0008"]
    for claimed in ["3500.00", "4000.00", "5050.00"]:
        add(
            user_id="u-zhoujie", policy_no="POL-2026-0008", claimed=claimed,
            incident_date="2026-05-12",
            incident_description=f"路口碰撞事故，本车维修费用{claimed}元，交警认定我方全责。",
            materials=auto_docs, category="normal", route="auto",
            liability="covered", approved=_approved(Decimal(claimed), auto_policy),
            sequence=FULL_SEQ, declared="auto",
            note="车险正常签发（金额阶梯）",
        )
    for claimed in ["8000.00", "15000.00"]:
        add(
            user_id="u-zhoujie", policy_no="POL-2026-0008", claimed=claimed,
            incident_date="2026-05-12",
            incident_description=f"追尾事故，本车维修费用{claimed}元，交警认定我方全责。",
            materials=auto_docs, category="normal", route="human",
            liability="covered", approved=_approved(Decimal(claimed), auto_policy),
            sequence=FULL_SEQ, declared="auto",
            note="车险金额超自动签发线，走完管线后签批转人工",
        )
    for desc in ["酒后驾驶发生单车事故，车辆损失3500元。", "无证驾驶碰撞护栏，维修费8000元。"]:
        assert any(kw in desc for kw in auto_kw), f"车险排除描述「{desc}」不含包排除关键词"
        add(
            user_id="u-zhoujie", policy_no="POL-2026-0008", claimed="8000.00",
            incident_date="2026-06-18",
            incident_description=desc,
            materials=auto_docs, category="rejected", route="auto",
            liability="not_covered", approved=Decimal("0.00"), sequence=FULL_SEQ,
            declared="auto", note="车险除外责任，标准拒赔",
        )
    add(
        user_id="u-zhoujie", policy_no="POL-2026-0008", claimed="8000.00",
        incident_date="2026-05-12",
        incident_description="追尾事故，本车维修费用8000元，交警认定我方全责。",
        materials=auto_docs[1:], category="missing", route="supplement",
        liability=None, approved=None, sequence=["material_review"], declared="auto",
        note="车险缺件：缺交通事故认定书",
    )

    # ---------- 财产险组（T120 上线，POL-2026-0009：免赔 0 / 比例 0.9 / 无等待期） ----------
    property_kw = [kw for kw, _ in PROPERTY_PACK.exclusion_keywords]
    property_policy = policies["POL-2026-0009"]
    for claimed in ["4000.00", "5000.00"]:
        add(
            user_id="u-wumin", policy_no="POL-2026-0009", claimed=claimed,
            incident_date="2026-04-20",
            incident_description=f"厨房火灾烧毁家电与家具，财产损失{claimed}元，消防已出具证明。",
            materials=property_docs, category="normal", route="auto",
            liability="covered", approved=_approved(Decimal(claimed), property_policy),
            sequence=FULL_SEQ, declared="property",
            note="财产险正常签发（金额阶梯）",
        )
    add(
        user_id="u-wumin", policy_no="POL-2026-0009", claimed="12000.00",
        incident_date="2026-04-20",
        incident_description="厨房火灾烧毁家电与家具，财产损失12000元，消防已出具证明。",
        materials=property_docs, category="normal", route="human",
        liability="covered", approved=_approved(Decimal("12000.00"), property_policy),
        sequence=FULL_SEQ, declared="property",
        note="财产险金额超自动签发线，走完管线后签批转人工",
    )
    for desc in ["地震导致房屋墙体开裂，维修损失12000元。", "家中被盗损失金银首饰一批，共计30000元。"]:
        assert any(kw in desc for kw in property_kw), f"财产险排除描述「{desc}」不含包排除关键词"
        add(
            user_id="u-wumin", policy_no="POL-2026-0009", claimed="12000.00",
            incident_date="2026-04-20",
            incident_description=desc,
            materials=property_docs, category="rejected", route="auto",
            liability="not_covered", approved=Decimal("0.00"), sequence=FULL_SEQ,
            declared="property", note="财产险除外责任，标准拒赔",
        )
    add(
        user_id="u-wumin", policy_no="POL-2026-0009", claimed="4000.00",
        incident_date="2026-04-20",
        incident_description="厨房火灾烧毁家电与家具，财产损失4000元，消防已出具证明。",
        materials=property_docs[:1], category="missing", route="supplement",
        liability=None, approved=None, sequence=["material_review"], declared="property",
        note="财产险缺件：缺损失清单、购置凭证",
    )

    # ---------- 意外险组（T120 上线，POL-2026-0010：免赔 100 / 比例 0.9 / 无等待期） ----------
    accident_kw = [kw for kw, _ in ACCIDENT_PACK.exclusion_keywords]
    accident_policy = policies["POL-2026-0010"]
    for claimed in ["2000.00", "3000.00"]:
        add(
            user_id="u-chenjing", policy_no="POL-2026-0010", claimed=claimed,
            incident_date="2026-07-02",
            incident_description=f"雨天路滑跌倒致手腕骨折，门诊治疗费用{claimed}元。",
            materials=accident_docs, category="normal", route="auto",
            liability="covered", approved=_approved(Decimal(claimed), accident_policy),
            sequence=FULL_SEQ, declared="accident",
            note="意外险正常签发（金额阶梯）",
        )
    add(
        user_id="u-chenjing", policy_no="POL-2026-0010", claimed="5000.00",
        incident_date="2026-07-02",
        incident_description="跌倒致踝骨骨折，治疗费用5000元，含自费药800元。",
        materials=accident_docs, category="partial", route="auto",
        liability="partial",
        approved=_approved(Decimal("5000.00"), accident_policy, Decimal("800.00")),
        sequence=FULL_SEQ, declared="accident",
        note="意外险部分责任：先扣自费再扣免赔乘比例",
    )
    add(
        user_id="u-chenjing", policy_no="POL-2026-0010", claimed="9000.00",
        incident_date="2026-07-02",
        incident_description="跌倒致腿部骨折住院，治疗费用9000元。",
        materials=accident_docs, category="normal", route="human",
        liability="covered", approved=_approved(Decimal("9000.00"), accident_policy),
        sequence=FULL_SEQ, declared="accident",
        note="意外险金额超自动签发线，走完管线后签批转人工",
    )
    for desc in ["潜水时发生耳膜穿孔，治疗费3000元。", "攀岩坠落导致骨折，治疗费9000元。"]:
        assert any(kw in desc for kw in accident_kw), f"意外险排除描述「{desc}」不含包排除关键词"
        add(
            user_id="u-chenjing", policy_no="POL-2026-0010", claimed="9000.00",
            incident_date="2026-07-02",
            incident_description=desc,
            materials=accident_docs, category="rejected", route="auto",
            liability="not_covered", approved=Decimal("0.00"), sequence=FULL_SEQ,
            declared="accident", note="意外险除外责任，标准拒赔",
        )
    # 退保保单拒赔（原"未上线转人工"案随 T120 上线转为走管线拒赔）
    add(
        user_id="u-chenjing", policy_no="POL-2023-0004", claimed="8600.00",
        incident_date="2026-08-25",
        incident_description="雨天摔倒致手腕骨折，门诊+住院费用8600元。",
        materials=accident_docs, category="rejected", route="auto",
        liability="not_covered", approved=Decimal("0.00"), sequence=FULL_SEQ,
        declared="accident",
        note="意外险保单已退保（surrendered），保障终止标准拒赔",
    )
    add(
        user_id="u-chenjing", policy_no="POL-2026-0010", claimed="3000.00",
        incident_date="2026-07-02",
        incident_description="雨天路滑跌倒致手腕骨折，门诊治疗费用3000元。",
        materials=accident_docs[2:], category="missing", route="supplement",
        liability=None, approved=None, sequence=["material_review"], declared="accident",
        note="意外险缺件：缺事故证明、诊断证明",
    )

    # ---------- 未上线/未分类边界（T120 后仅 unknown：重疾险不在任何 pack） ----------
    offline = [
        ("u-lina", "POL-2025-0002", None, "确诊重大疾病，申请理赔。"),
        ("u-lina", "POL-2025-0002", None, "轻度脑中风后遗症理赔申请。"),
    ]
    for user, policy_no, declared, desc in offline:
        add(
            user_id=user, policy_no=policy_no, claimed="8600.00",
            incident_date="2026-08-25", incident_description=desc,
            materials=[], category="intake", route="human", liability=None,
            approved=None, sequence=[],
            note="产品类型不在任何上线 pack（unknown），受理转人工", declared=declared,
        )

    # ---------- 补充边界 ----------
    for _i, (claimed, _selfpay, note_desc) in enumerate([
        ("10001.00", "0", "免赔额临界 +1 元"),
        ("9999.99", "0", "免赔额以下"),
        ("10002.50", "0", "免赔额临界小额"),
    ]):
        add(
            user_id="u-zhangwei", policy_no="POL-2025-0001", claimed=claimed,
            incident_date="2026-08-10",
            incident_description=f"肺炎住院治疗，费用{claimed}元。",
            materials=full_docs, category="edge", route="auto",
            liability="covered",
            approved=_approved(Decimal(claimed), policies["POL-2025-0001"]),
            sequence=FULL_SEQ, note=note_desc,
        )

    # 材料矛盾（金额不一致）× 3
    for _i, (inv, cost) in enumerate([("15800.00", "12800.00"), ("8800.00", "6600.00"), ("20000.00", "15500.00")]):
        add(
            user_id="u-zhangwei", policy_no="POL-2025-0001", claimed=inv,
            incident_date="2026-08-10",
            incident_description=f"急性阑尾炎手术申请理赔。发票金额{inv}元与费用清单{cost}元不一致。",
            materials=[
                {"file_name": "invoice.jpg", "doc_type": "invoice"},
                {"file_name": "diagnosis.jpg", "doc_type": "diagnosis"},
                {"file_name": "cost_list.pdf", "doc_type": "cost_list", "note": f"清单金额{cost}，与发票矛盾"},
            ],
            category="edge", route="human", liability=None, approved=None,
            sequence=["material_review"], note=f"材料矛盾（发票{inv} vs 清单{cost}）",
        )

    # 部分责任 + 缺件复合（先补件后 partial）
    add(
        user_id="u-liuyang", policy_no="POL-2026-0005", claimed="16000.00",
        incident_date="2026-09-02",
        incident_description="腹腔镜胆囊切除，含乙类药自付3000元。",
        materials=[
            {"file_name": "diagnosis.jpg", "doc_type": "diagnosis"},
            {"file_name": "cost_list_b.pdf", "doc_type": "cost_list"},
        ],
        category="missing", route="supplement", liability=None, approved=None,
        sequence=["material_review"],
        note="缺医疗发票，先补件；补齐后走 partial 路径",
        added_materials=[{"file_name": "invoice_16000.jpg", "doc_type": "invoice"}],
    )

    cases.extend(generated)

    meta = {
        "task": "T088 核赔金样本评测集",
        "base_cases": "data/mock/cases.json（CASE-2026-0001..0024 原样收录）",
        "金额公式": (
            "approved = min(max(claimed − selfpay − deductible, 0) × ratio, coverage)；"
            "等待期 30 天（生效日+offset 计数，第 31 天起可赔）；"
            "自动签发线 5000 元（低风险+责任明确前置）"
        ),
        "frequency_signals": [
            {
                "id_card": "330103198805126778",
                "holder_name": "孙强",
                "claims_days_ago": [10, 30],
                "policy_no": "POL-2026-0007",
            }
        ],
        "说明": (
            "frequency_signals 由评测运行器按相对天数换算绝对日期入库（T089），"
            "使高频期望不随评测执行时间漂移；黑名单走 data/mock/blacklist.json"
        ),
    }
    out = {"_meta": meta, "cases": cases, "frequency_signals": meta["frequency_signals"]}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    by_cat: dict[str, int] = {}
    for c in cases:
        by_cat[c["expected"]["category"]] = by_cat.get(c["expected"]["category"], 0) + 1
    print(f"数据集生成：{len(cases)} 案件 → {OUT_PATH.relative_to(ROOT)}")
    for cat, n in sorted(by_cat.items()):
        print(f"  {cat}: {n}")


if __name__ == "__main__":
    main()
