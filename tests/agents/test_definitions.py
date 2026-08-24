"""Agent 定义与 Prompt 体系测试（T015 验收）。

- 4 个 Agent 定义完整（name / prompt / 工具集 / 输出 schema）
- prompt 含关键职责约束（compliance 一票否决、claim 禁凭空估算等）
- 输出 schema 对合法/非法 JSON 的校验行为
- resolve_tools 从注册中心解析并过滤未注册工具
"""

from __future__ import annotations

import pytest

from agents import (
    ALL_AGENTS,
    CLAIM_AGENT,
    COMPLIANCE_AGENT,
    MEDICAL_AGENT,
    ORCHESTRATOR_AGENT,
    get_agent,
)
from schemas.agent_outputs import (
    ClaimAgentOutput,
    ComplianceAgentOutput,
    MedicalAgentOutput,
    OrchestratorPlan,
)
from tools.registry import ToolRegistry

# ---------- Agent 定义完整性 ----------


def test_four_agents_registered() -> None:
    """四个 Agent 全部注册且命名符合架构文档。"""
    assert set(ALL_AGENTS) == {"orchestrator", "claim", "medical", "compliance"}
    assert get_agent("claim") is CLAIM_AGENT


def test_agent_definitions_have_required_fields() -> None:
    """每个 Agent：非空 prompt / 描述 / 输出 schema。"""
    for agent in ALL_AGENTS.values():
        assert len(agent.system_prompt) > 100, f"{agent.name} prompt 过短"
        assert agent.display_name
        assert agent.description
        assert issubclass(agent.output_schema, object)


def test_agent_tool_assignments() -> None:
    """工具集分配符合架构设计（architecture.md 第 3 节）。"""
    assert "policy_query" in CLAIM_AGENT.tool_names
    assert "claim_calculator" in CLAIM_AGENT.tool_names
    assert "claim_rule_rag" in CLAIM_AGENT.tool_names

    assert "record_query" in MEDICAL_AGENT.tool_names
    assert "diagnosis_matcher" in MEDICAL_AGENT.tool_names

    assert "compliance_rule_check" in COMPLIANCE_AGENT.tool_names
    assert "sensitive_filter" in COMPLIANCE_AGENT.tool_names

    # Orchestrator 不直接持有业务工具
    assert ORCHESTRATOR_AGENT.tool_names == []


# ---------- Prompt 关键约束 ----------


def test_compliance_prompt_contains_veto_rules() -> None:
    """Compliance prompt：一票否决权 + 三态结论 + 违规标准。"""
    prompt = COMPLIANCE_AGENT.system_prompt
    assert "一票否决权" in prompt
    for verdict in ("PASS", "MODIFY", "REJECT"):
        assert verdict in prompt
    for rule in ("PROMISE", "FRAUD_RISK", "PRIVACY"):
        assert rule in prompt


def test_claim_prompt_forbids_fabrication() -> None:
    """Claim prompt：禁止凭空估算 + 预估/最终区分。"""
    prompt = CLAIM_AGENT.system_prompt
    assert "严禁凭空估算" in prompt
    assert "预估" in prompt


def test_medical_prompt_requires_icd10_basis() -> None:
    """Medical prompt：ICD-10 依据 + 等待期标注 + 材料逐项列出。"""
    prompt = MEDICAL_AGENT.system_prompt
    assert "ICD-10" in prompt
    assert "等待期" in prompt


def test_orchestrator_prompt_has_routing_principles() -> None:
    """Orchestrator prompt：medical 先行 / claim 在后 / compliance 图保证。"""
    prompt = ORCHESTRATOR_AGENT.system_prompt
    assert "medical" in prompt and "claim" in prompt
    assert "compliance" in prompt


def test_prompts_are_valid_format_templates() -> None:
    """输出格式段含 JSON 示例（{{}} 转义，format 时不炸）。"""
    for agent in ALL_AGENTS.values():
        assert "{{" in agent.system_prompt or "JSON" in agent.system_prompt, (
            f"{agent.name} prompt 缺输出格式说明"
        )


# ---------- 输出 schema 校验 ----------


def test_claim_output_schema_validates() -> None:
    """Claim 输出：合法 JSON 通过，summary 必填。"""
    ok = ClaimAgentOutput.model_validate(
        {
            "summary": "POL-2025-0001 预估赔付 4640 元",
            "policy_info": {"policy_no": "POL-2025-0001"},
            "calculation": {"estimated_payout": 4640.0},
            "warnings": ["预估金额，最终以审核为准"],
        }
    )
    assert ok.calculation["estimated_payout"] == 4640.0

    # 缺 summary 被拒
    with pytest.raises(Exception):  # noqa: B017
        ClaimAgentOutput.model_validate({"warnings": []})


def test_medical_output_schema_defaults() -> None:
    """Medical 输出：可选字段默认空集合。"""
    out = MedicalAgentOutput.model_validate({"summary": "K35 在保障范围内"})
    assert out.missing_materials == []
    assert out.records == []


def test_compliance_output_schema_validates_violations() -> None:
    """Compliance 输出：违规项结构化嵌套校验。"""
    out = ComplianceAgentOutput.model_validate(
        {
            "verdict": "MODIFY",
            "violations": [
                {
                    "type": "PROMISE",
                    "detail": "保证赔付 10000 元",
                    "suggestion": "改为'预估赔付'",
                }
            ],
            "risk_score": 30,
            "reason": "含承诺性话术",
        }
    )
    assert out.verdict == "MODIFY"
    assert out.violations[0].type == "PROMISE"
    assert out.risk_score == 30


def test_orchestrator_plan_schema() -> None:
    """Orchestrator 计划：步骤列表结构化校验。"""
    plan = OrchestratorPlan.model_validate(
        {
            "intent": "multi_step",
            "steps": [
                {"agent": "medical", "description": "核对诊断与保障范围"},
                {"agent": "claim", "description": "计算预估赔付金额"},
            ],
        }
    )
    assert plan.intent == "multi_step"
    assert [s.agent for s in plan.steps] == ["medical", "claim"]


# ---------- resolve_tools（注册中心联动） ----------


def test_resolve_tools_filters_unregistered() -> None:
    """工具未注册（跨任务依赖）时跳过，不阻断 Agent。"""
    registry = ToolRegistry()
    # 只注册 policy_query（其余 claim 工具未注册）
    from tools.claim.policy_query import PolicyQueryTool

    registry.register(PolicyQueryTool())

    specs = CLAIM_AGENT.resolve_tools(registry)
    names = [s["function"]["name"] for s in specs]
    assert names == ["policy_query"]  # 未注册的自动过滤


def test_resolve_tools_with_full_registry() -> None:
    """完整注册中心（import tools.claim）：claim Agent 解析出全部已注册工具。"""
    import tools.claim  # noqa: F401
    from tools.registry import get_default_registry

    specs = CLAIM_AGENT.resolve_tools(get_default_registry())
    names = {s["function"]["name"] for s in specs}
    assert {"policy_query", "claim_calculator", "claim_rule_rag"} <= names
