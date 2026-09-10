"""orchestrator 前置条件守卫与确定性兜底编排测试（T079，D039 安全设计 2/3）。

守卫规则逐条验证：违规改投 / 残缺丢弃 / 去重 / 补件重跑放行 / decision_generate
单派 + 必做集 / 并行依赖消解；default_route 主线推进与人工分流。
"""

from __future__ import annotations

from nodes.guards import default_route, enforce_guards


def s(**kw):
    """构造最小案件状态。"""
    return {"case_id": "CASE-TEST", **kw}


COMPLETE = {"completeness": "complete", "missing": [], "confidence": 1.0}
PARTIAL = {"completeness": "partial", "missing": ["费用清单"], "confidence": 1.0}


def test_material_missing_redirect() -> None:
    """材料未审就派保单核验 → 改投 material_review。"""
    verdict = enforce_guards(["policy_verify"], s())
    assert verdict.targets == ["material_review"]
    assert verdict.corrected
    assert any(n.startswith("redirect:") for n in verdict.notes)


def test_material_partial_drops_query_worker() -> None:
    """材料残缺（待补件）时派查询 worker → 丢弃（重跑无意义，走 human 通道）。"""
    verdict = enforce_guards(["fraud_check"], s(material=PARTIAL))
    assert verdict.targets == []
    assert any(n.startswith("drop_blocked:") for n in verdict.notes)


def test_done_worker_dropped() -> None:
    """重派已完成 worker → 丢弃（去重）。"""
    verdict = enforce_guards(["material_review"], s(material=COMPLETE))
    assert verdict.targets == []
    assert any(n.startswith("drop_done:") for n in verdict.notes)


def test_redo_allowed_after_supplement_resume() -> None:
    """补件恢复（human_resolution 在）→ 允许重跑材料审核。"""
    state = s(material=COMPLETE, human_resolution={"kind": "supplement"})
    verdict = enforce_guards(["material_review"], state)
    assert verdict.targets == ["material_review"]
    assert not any(n.startswith("drop_done:") for n in verdict.notes)


def test_decision_generate_solo_only() -> None:
    """decision_generate 必须单派：与重跑的材料审核同批时被顺延。"""
    state = s(
        material=COMPLETE,
        policy={"x": 1},
        risk={"x": 1},
        liability={"x": 1},
        calc={"x": 1},
        human_resolution={"kind": "supplement"},
    )
    verdict = enforce_guards(["material_review", "decision_generate"], state)
    assert verdict.targets == ["material_review"]
    assert "defer_decision_generate:solo_only" in verdict.notes


def test_decision_generate_must_complete_set() -> None:
    """必做集未全 done → 改投首个缺失项（此例缺理算）。"""
    state = s(
        material=COMPLETE,
        policy={"x": 1},
        risk={"x": 1},
        liability={"x": 1},
    )
    verdict = enforce_guards(["decision_generate"], state)
    assert verdict.targets == ["amount_calc"]
    assert any("redirect:decision_generate->amount_calc" in n for n in verdict.notes)


def test_parallel_dependency_resolved() -> None:
    """并行批次含依赖冲突（责任认定依赖保单/风控结论）→ 改投收敛为单目标。"""
    state = s(material=COMPLETE)
    verdict = enforce_guards(["policy_verify", "liability_judge"], state)
    assert verdict.targets == ["policy_verify"]
    assert verdict.corrected


def test_prerequisite_redirect_chain() -> None:
    """理算缺失责任结论 → 改投 liability_judge；再派理算时责任在 → 可派。"""
    state = s(material=COMPLETE, policy={"x": 1}, risk={"x": 1})
    first = enforce_guards(["amount_calc"], state)
    assert first.targets == ["liability_judge"]

    state2 = s(material=COMPLETE, policy={"x": 1}, risk={"x": 1}, liability={"x": 1})
    second = enforce_guards(["amount_calc"], state2)
    assert second.targets == ["amount_calc"]
    assert not second.corrected


def test_default_route_progression() -> None:
    """兜底编排主线：材料 → 并行（保单∥风控）→ 责任 → 理算 → 决定书。"""
    assert default_route(s()) == (["material_review"], None)
    assert default_route(s(material=PARTIAL))[1]["kind"] == "supplement"
    assert default_route(s(material=PARTIAL))[0] == ["human_gate"]

    low_conf = {"completeness": "complete", "missing": [], "confidence": 0.4}
    targets, human = default_route(s(material=low_conf))
    assert targets == ["human_gate"] and human["kind"] == "review"

    targets, human = default_route(s(material=COMPLETE))
    assert targets == ["policy_verify", "fraud_check"] and human is None

    state = s(material=COMPLETE, policy={"x": 1})
    assert default_route(state)[0] == ["fraud_check"]
    state["risk"] = {"risk_level": "low"}
    assert default_route(state)[0] == ["liability_judge"]
    state["liability"] = {"verdict": "covered"}
    assert default_route(state)[0] == ["amount_calc"]
    state["calc"] = {"approved_amount": "0"}
    assert default_route(state)[0] == ["decision_generate"]


def test_default_route_human_branches() -> None:
    """高风险短路 / 全 done 防御性转人工。"""
    targets, human = default_route(
        s(material=COMPLETE, policy={"x": 1}, risk={"risk_level": "high"})
    )
    assert targets == ["human_gate"] and human["kind"] == "review"

    everything = s(
        material=COMPLETE,
        policy={"x": 1},
        risk={"risk_level": "low"},
        liability={"verdict": "covered"},
        calc={"approved_amount": "100"},
        decision={"x": 1},
    )
    targets, human = default_route(everything)
    assert targets == ["human_gate"] and human["kind"] == "review"
