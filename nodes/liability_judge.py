"""责任认定节点（F07，T084 真实化）：create_agent ReAct 子图（条款 RAG + 诊断匹配）。

三段判定：
1. 确定性前置（不经 LLM）——保单无效 / 等待期未过 → 直接 not_covered
   （保单事实是确定性数据，D039：判断交给数据，不交给概率）
2. LLM 裁定（AgentDefinition + create_agent：claim_rule_rag 条款检索 +
   diagnosis_matcher 诊断匹配，response_format = LiabilityOutput）
3. 关键词兜底（LLM 不可用/失败 → T079 规则：除外关键词 + 自费金额识别，
   D012 兜底判定思想；规则数据由险种 pack 承载，T096）

LLM 注入约定（T096 统一，四节点一致）：llm=None = 确定性路径（零 LLM），
callable = async (agent_def, instruction) -> (result, new_messages)。

工具轨迹随 worker 新增消息并入主图 messages（derive_tool_trace 可派生），
并摘要写入 stage_result 审计事件。
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from schemas.lines import get_line_pack
from schemas.stages import LiabilityOutput
from services.case_store import CaseRecorder
from services.llm.prompts import CASE_LIABILITY_AGENT_PROMPT
from services.skills import build_system_prompt
from services.worker_agent import AgentDefinition, derive_tool_trace
from state import ClaimCaseState

log = get_logger(__name__)

_EXCLUSION_CLAUSE = "第五条 责任免除"
_COVERED_CLAUSE = "第三条 保险责任"

# 责任认定 Agent 工具集（医疗线加诊断匹配；按 pack 声明式承载，T120）
LIABILITY_TOOL_NAMES = ["claim_rule_rag", "diagnosis_matcher"]

# Agent 定义按险种线缓存（skill 装载随线别；子图编译本身在 worker_agent 层缓存）
_agent_defs: dict[str, AgentDefinition] = {}


def _agent_def_for(line: str) -> AgentDefinition:
    """按险种线装配责任认定 Agent 定义（多险种 pack 上线时按 case_type 分定义）。"""
    if line not in _agent_defs:
        pack = get_line_pack(line)
        _agent_defs[line] = AgentDefinition(
            name="liability_judge",
            display_name="责任认定专员",
            system_prompt=build_system_prompt(
                CASE_LIABILITY_AGENT_PROMPT, "liability_judge", line
            ),
            tool_names=list(pack.liability_tools) if pack else list(LIABILITY_TOOL_NAMES),
            output_schema=LiabilityOutput,
            description="判定出险是否属于保险责任范围，输出结论/条款引用/置信度",
        )
    return _agent_defs[line]


def reset_agent_defs() -> None:
    """清空 Agent 定义缓存（测试用：skill 文本变更后重建）。"""
    _agent_defs.clear()


def _case_facts(state: ClaimCaseState) -> str:
    """案件事实（Agent 输入）：出险描述 + 已提取材料字段 + 保单要点。"""
    policy = state.get("policy") or {}
    material = state.get("material") or {}
    docs = [
        {
            "doc_type": d.get("doc_type"),
            "diagnosis": d.get("diagnosis"),
            "total_amount": str(d.get("total_amount") or ""),
            "patient_name": d.get("patient_name"),
        }
        for d in material.get("documents", [])
        if isinstance(d, dict)
    ]
    facts = {
        "incident_description": str(state.get("incident_description", ""))[:500],
        "extracted_documents": docs,
        "policy": {
            "product_name": policy.get("product_name") if policy else None,
            "coverage_valid": policy.get("coverage_valid") if policy else None,
            "exclusions": policy.get("exclusions", []) if policy else [],
        },
    }
    return json.dumps(facts, ensure_ascii=False, default=str)


def _keyword_fallback(state: ClaimCaseState) -> LiabilityOutput:
    """关键词规则兜底（T079 规则保留：LLM 不可用时的最后防线）。

    除外关键词/自费识别正则由案件险种的 pack 承载（T096）；
    无 pack（unknown，正常不会到达）→ 仅按保单事实判定。
    """
    description = str(state.get("incident_description", ""))
    policy_out = state.get("policy") or {}
    pack = get_line_pack(state.get("case_type"))

    verdict = "covered"
    reason = "出险事件属于保险责任范围"
    exclusions: list[str] = []
    self_pay: Decimal | None = None
    clauses = [_COVERED_CLAUSE]

    if not policy_out.get("coverage_valid", False):
        verdict = "not_covered"
        reason = str(policy_out.get("invalid_reason") or "保单保障无效")
        clauses = [_EXCLUSION_CLAUSE]
    else:
        for keyword, exclusion in (pack.exclusion_keywords if pack else ()):
            if keyword in description:
                verdict = "not_covered"
                reason = f"「{exclusion}」属于责任免除范围"
                exclusions.append(exclusion)
                clauses = [_EXCLUSION_CLAUSE]
                break

    if verdict == "covered" and pack is not None and pack.self_pay_pattern:
        match = re.search(pack.self_pay_pattern, description)
        if match is not None:
            self_pay = Decimal(match.group(1))
            verdict = "partial"
            reason = f"属于保险责任范围，但含自费/乙类自付项目 {self_pay} 元需扣除"

    return LiabilityOutput(
        verdict=verdict,  # type: ignore[arg-type]
        reason=reason,
        clause_references=clauses,
        exclusions_triggered=exclusions,
        self_pay_amount=self_pay,
        confidence=0.9,  # 规则判定可靠；须过自动签发置信度门槛（auto_approve_confidence_floor）
    )


def _policy_precheck(state: ClaimCaseState) -> LiabilityOutput | None:
    """确定性前置：保单无效/等待期未过 → 直接 not_covered（不经 LLM）。"""
    policy_out = state.get("policy") or {}
    if not policy_out.get("coverage_valid", False):
        return LiabilityOutput(
            verdict="not_covered",
            reason=str(policy_out.get("invalid_reason") or "保单保障无效"),
            clause_references=[_EXCLUSION_CLAUSE],
            confidence=1.0,
        )
    if policy_out.get("waiting_period_passed") is False:
        return LiabilityOutput(
            verdict="not_covered",
            reason="出险日在等待期内，属于责任免除范围",
            clause_references=[_EXCLUSION_CLAUSE],
            confidence=1.0,
        )
    return None


def make_liability_judge_node(recorder: CaseRecorder, llm=None, agent_def=None):
    """责任认定节点工厂。

    llm：async (agent_def, instruction) -> (result, new_messages)；
    None = 确定性关键词兜底（T096 统一约定，测试/降级零 LLM）。
    agent_def 测试可注入替身；生产按案件险种线懒装配（_agent_def_for）。
    """

    async def liability_judge_node(state: ClaimCaseState) -> dict[str, Any]:
        # 1) 确定性前置：保单事实不经 LLM
        precheck = _policy_precheck(state)
        new_messages: list[Any] = []
        tools_used: list[str] = []
        if precheck is not None:
            output = precheck
        elif llm is None:
            # 2') 确定性路径：关键词兜底（T096 统一 None 语义，非异常驱动）
            output = _keyword_fallback(state)
        else:
            # 2) LLM 裁定（ReAct：条款检索 + 诊断匹配）
            agent = agent_def or _agent_def_for(str(state.get("case_type") or "unknown"))
            try:
                result, new_messages = await llm(agent, _case_facts(state))
                output = LiabilityOutput.model_validate(
                    {k: v for k, v in result.items()
                     if k in LiabilityOutput.model_fields}
                )
                tools_used = [t["tool"] for t in derive_tool_trace(new_messages)]
            except Exception as exc:  # noqa: BLE001  LLM 失败走关键词兜底（D012 思想）
                log.warning("liability_llm_failed",
                            case_id=state["case_id"], error=str(exc)[:200])
                output = _keyword_fallback(state)

        dump = output.model_dump(mode="json")
        await recorder.event(
            state["case_id"],
            "stage_result",
            stage="liability_judge",
            payload={**dump, "tools_used": tools_used},
        )
        updates: dict[str, Any] = {"liability": dump}
        if new_messages:
            # 工具轨迹并入主图 messages（derive_tool_trace 可派生，验收口径）
            updates["messages"] = new_messages
        return updates

    return liability_judge_node
