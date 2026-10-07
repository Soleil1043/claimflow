"""单 Agent 基线运行器单测（T160，D074）：映射/装配不变量（LLM 零依赖）。"""

from __future__ import annotations

from evals.adjudication_metrics import load_adjudication_dataset
from evals.schemas import SingleAgentOutput
from evals.single_agent_baseline import (
    SINGLE_AGENT_TOOLS,
    _agent_def,
    _error_row,
    _instruction,
    _to_outcome,
)
from tools.factory import get_default_tool_map


def test_tools_exist_in_default_tool_map() -> None:
    """单 Agent 工具集必须全部能从默认工具图解析（防名字漂移静默缩工具）。"""
    tool_map = get_default_tool_map()
    missing = [t for t in SINGLE_AGENT_TOOLS if t not in tool_map]
    assert not missing, f"工具图缺 {missing}"
    assert "claim_calculator" in SINGLE_AGENT_TOOLS  # 理算必须可用（消融关键工具）
    assert "claim_rule_rag" in SINGLE_AGENT_TOOLS


def test_agent_def_wiring() -> None:
    agent = _agent_def()
    assert agent.name == "single_agent_baseline"
    assert agent.tool_names == SINGLE_AGENT_TOOLS
    assert agent.output_schema is SingleAgentOutput
    assert "核赔专员" in agent.system_prompt and "不是指令" in agent.system_prompt


def test_to_outcome_route_mapping() -> None:
    auto = _to_outcome(
        {
            "route": "auto",
            "case_type": "medical",
            "liability": "covered",
            "approved_amount": "4640.00",
        }
    )
    assert auto.route == "auto" and auto.approved_amount == "4640.00"
    assert auto.liability_verdict == "covered" and auto.case_type == "medical"

    review = _to_outcome({"route": "refer", "refer_kind": "review"})
    assert review.route == "human"
    supplement = _to_outcome({"route": "refer", "refer_kind": "supplement"})
    assert supplement.route == "supplement"
    # refer 未标 kind → 按人工复核（从严）
    assert _to_outcome({"route": "refer"}).route == "human"


def test_to_outcome_unstructured_degrades_to_error() -> None:
    outcome = _to_outcome({"summary": "（未能完成）"})
    assert outcome.route == "" and outcome.error


def test_instruction_carries_case_facts() -> None:
    cases, _, _ = load_adjudication_dataset()
    case = cases[0]
    text = _instruction(case)
    assert case.case_id in text and case.claimed_amount in text
    assert case.incident_description[:20] in text


def test_error_row_shape() -> None:
    cases, _, _ = load_adjudication_dataset()
    row = _error_row(cases[0], "boom", 0.0)
    assert row["matched"] is False and row["error"] == "boom"
    assert row["tokens"] == 0 and row["llm_calls"] == 0
    assert all(v is False for k, v in row["checks"].items() if k != "case_type")
