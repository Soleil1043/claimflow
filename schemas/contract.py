"""核赔规格契约（T097，评审候选 5）：运行时 / 金样本生成器 / 评测门的唯一数值来源。

同一个数字只能在这里定义一次：
- app/core/config.py 的核赔阈值默认值引用本模块（运行时可经环境变量覆盖，
  但金样本期望按本契约的规范值生成——覆盖默认值会使评测门暴露漂移）
- scripts/gen_adjudication_cases.py 生成的金样本期望按本契约计算
- evals/gates.py 的门禁阈值引用本模块（评测门 = 运行时门）
- schemas/lines.py 医疗险 pack 的等待期引用本模块

skill 散文中的对应数字是人工调优面（D040），不由代码生成；漂移由金样本
路由一致率软门兜住。
"""

from __future__ import annotations

from decimal import Decimal

# 自动签发金额上限：核定金额 ≤ 该值且低风险才允许自动出《理赔决定书》
AUTO_APPROVE_LIMIT = Decimal("5000.00")

# 医疗险等待期天数（保单条款要素；出险日 ≤ 生效日+天数 → 责任免除）
WAITING_PERIOD_DAYS = 30

# orchestrator 每案件调度调用预算（D039 防绕圈；超限告警并强制收敛）
ROUTING_CALL_BUDGET = 15


# 终态结论判定（T107 单源）：auto_adjudicate 签发分支与评测记忆种子共用同一规则，
# 防止两处手抄漂移（评审二候选 5：种子档案 outcome/decision 曾自相矛盾）
_FINAL_DECISION_BY_VERDICT = {
    "not_covered": "rejected",
    "partial": "partial",
    "covered": "approved",
}


def final_decision_from_verdict(verdict: str) -> str:
    """责任结论 → 终态结论（规格规则；未知 verdict 按 covered 处理不抛错）。"""
    return _FINAL_DECISION_BY_VERDICT.get(verdict, "approved")
