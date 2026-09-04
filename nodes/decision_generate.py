"""决定书生成节点（F09，T085 真实化）。

金额安全设计（services.decision_doc）：正文骨架代码渲染——结论/核定金额/编号由
结构化数据注入；本节点只负责撰写"核定依据叙述段"（LLM + skill 装配，叙述不含
金额），LLM 失败回退规则版叙述（fail-open）。决定书版本化落库（save_decision）。

writer 注入三态（与 orchestrator/material 模式一致）：
- "__fallback__" → 纯代码叙述（build_case_graph 默认，工作流测试零 LLM）
- None → 真实 LLM 撰写（make_decision_writer，受 decision_writer_llm_enabled 开关）
- callable → 注入撰写器（测试脚本化）
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from langchain_core.messages import HumanMessage

from app.core.config import settings
from app.core.logging import get_logger
from services.case_store import CaseRecorder
from services.decision_doc import render_decision_document
from services.llm.client import get_chat_model
from services.llm.prompts import DECISION_NARRATIVE_PROMPT
from services.skills import build_system_prompt
from state import ClaimCaseState

log = get_logger(__name__)

FALLBACK_WRITER = "__fallback__"


def _narrative_facts(state: ClaimCaseState) -> str:
    """叙述撰写的事实输入：责任认定 + 材料审核要点（不含金额）。"""
    liability = state.get("liability") or {}
    material = state.get("material") or {}
    facts = {
        "incident_description": str(state.get("incident_description", ""))[:300],
        "verdict": liability.get("verdict"),
        "reason": liability.get("reason"),
        "clause_references": liability.get("clause_references", []),
        "completeness": material.get("completeness"),
    }
    return json.dumps(facts, ensure_ascii=False, default=str)


def make_decision_writer():
    """叙述撰写器工厂（LLM + skill 装配）。

    settings.decision_writer_llm_enabled=False → None（纯代码叙述）。
    返回 async (state) -> str | None（None=撰写失败，调用方回退规则叙述）。
    """
    if not settings.decision_writer_llm_enabled:
        return None

    async def writer(state: ClaimCaseState) -> str | None:
        system = build_system_prompt(
            DECISION_NARRATIVE_PROMPT,
            "decision_writer",
            state.get("case_type") or "_shared",
            facts=_narrative_facts(state),
        )
        model = get_chat_model(temperature=0.0)
        response = await model.ainvoke([HumanMessage(content=system)])
        text = str(response.content or "").strip()
        return text or None

    return writer


def make_decision_generate_node(recorder: CaseRecorder, writer: Any = FALLBACK_WRITER):
    """决定书生成节点工厂（writer 三态见模块 docstring）。"""

    async def decision_generate_node(state: ClaimCaseState) -> dict[str, Any]:
        liability = state.get("liability") or {}
        calc = state.get("calc") or {}

        narrative: str | None = None
        if writer != FALLBACK_WRITER:
            writer_fn = writer if callable(writer) else make_decision_writer()
            if writer_fn is not None:
                try:
                    narrative = await writer_fn(state)
                except Exception as exc:  # noqa: BLE001——叙述失败回退规则版（fail-open）
                    log.warning("decision_narrative_failed",
                                case_id=state["case_id"], error=str(exc)[:200])
                    narrative = None

        doc = render_decision_document(
            case_id=state["case_id"],
            case_type=str(state.get("case_type") or "medical"),
            liability=liability,
            calc=calc,
            narrative=narrative,
            version=1,
            issued_by="auto",
        )

        await recorder.event(
            state["case_id"],
            "stage_result",
            stage="decision_generate",
            payload=doc,
        )
        # 决定书落库（版本化）：API/工作台查询与坐席改判追溯的事实态
        await recorder.save_decision(
            state["case_id"],
            title=doc["title"],
            body=doc["body"],
            conclusion=doc["conclusion"],
            approved_amount=Decimal(str(doc["approved_amount"] or "0")),
        )
        return {"decision": doc}

    return decision_generate_node
