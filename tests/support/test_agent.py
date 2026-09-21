"""客服 Agent 装配与对话轮测试（T133）：工具单测 + mock LLM 链路 + prompt 边界断言。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel

import services.support.agent as sa
from services.db.models import Case, CaseEvent, CaseJob, DecisionDocument, SupportMessage
from services.llm.prompts import SUPPORT_AGENT_PROMPT
from services.support import store
from services.support.agent import (
    SUPPORT_TOOL_NAMES,
    _replay_messages,
    reply,
    reset_support_agent_cache,
)
from tools.claim.case_status import CaseStatusQueryTool
from tools.claim.claim_draft import ClaimDraftLinkTool
from tools.support.escalate import EscalateToHumanTool


class _RagInput(BaseModel):
    query: str


class FakeRagTool(BaseTool):
    """claim_rule_rag 测试替身（不触达真实 Qdrant / Embedding）。"""

    name: str = "claim_rule_rag"
    description: str = "测试替身：知识库检索"
    args_schema: type[BaseModel] = _RagInput

    def _run(self, **kwargs: Any) -> Any:
        raise NotImplementedError("仅支持异步")

    async def _arun(self, **kwargs: Any) -> dict[str, Any]:
        return {"success": True, "results": [{"text": "等待期 30 天", "title": "条款"}]}


class ScriptedModel(GenericFakeChatModel):
    """脚本化模型：忽略 create_agent 的工具绑定（按预排消息应答）。"""

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedModel:  # noqa: ARG002
        return self


def _tool_call(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def _fake_tool_map() -> dict[str, BaseTool]:
    """客服四件套：RAG 用替身，其余三件真实（轻依赖）。"""
    return {
        "claim_rule_rag": FakeRagTool(),
        "case_status_query": CaseStatusQueryTool(),
        "claim_draft_link": ClaimDraftLinkTool(),
        "escalate_to_human": EscalateToHumanTool(),
    }


@pytest.fixture()
def agent_env(monkeypatch: pytest.MonkeyPatch):
    """脚本化模型 + 假工具图；agent 子图缓存用后复位。"""

    def make_model(messages: list[AIMessage]) -> None:
        monkeypatch.setattr(
            sa,
            "get_chat_model",
            lambda temperature=0.1: ScriptedModel(messages=iter(messages)),
        )
        monkeypatch.setattr(sa, "get_default_tool_map", _fake_tool_map)
        reset_support_agent_cache()

    yield make_model
    reset_support_agent_cache()


# ---------- prompt 边界与装配（T133 验收：边界有断言） ----------


def test_support_prompt_boundaries() -> None:
    """prompt 承载四能力工具指引与服务边界话术。"""
    for phrase in (
        "claim_rule_rag",
        "case_status_query",
        "claim_draft_link",
        "escalate_to_human",
        "不承诺赔付结果",
        "不引用内部阈值",
        "禁止编造",
        "转人工",
    ):
        assert phrase in SUPPORT_AGENT_PROMPT
    assert len(SUPPORT_TOOL_NAMES) == 4


# ---------- 对话轮 ----------


async def test_reply_roundtrip_persists(agent_env, support_db) -> None:
    """普通轮：返回终局文本，user/assistant 双双落库，会话仍 ai。"""
    conv = await store.create_conversation()
    agent_env([AIMessage(content="您好！请问有什么可以帮您？")])

    text = await reply(conv.id, "你好，我想咨询理赔")

    assert text == "您好！请问有什么可以帮您？"
    msgs = await store.list_messages(conv.id)
    assert [(m.role, m.content) for m in msgs] == [
        (store.ROLE_USER, "你好，我想咨询理赔"),
        (store.ROLE_ASSISTANT, "您好！请问有什么可以帮您？"),
    ]
    assert (await store.get_conversation(conv.id)).status == store.CONV_AI


async def test_reply_with_tool_call(agent_env, support_db) -> None:
    """工具轮：RAG 工具真实执行（替身），终局话术落库。"""
    conv = await store.create_conversation()
    agent_env(
        [
            _tool_call("claim_rule_rag", {"query": "等待期多久"}, "c1"),
            AIMessage(content="这款产品的等待期是 30 天。"),
        ]
    )

    text = await reply(conv.id, "等待期多久？")

    assert text == "这款产品的等待期是 30 天。"
    assert (await store.get_conversation(conv.id)).status == store.CONV_AI


async def test_reply_empty_output_fallback(agent_env, support_db) -> None:
    """模型终局无内容 → 兜底话术落库（不抛错）。"""
    conv = await store.create_conversation()
    agent_env([AIMessage(content="")])

    text = await reply(conv.id, "在吗")

    assert text
    msgs = await store.list_messages(conv.id)
    assert msgs[-1].role == store.ROLE_ASSISTANT
    assert msgs[-1].content == text


async def test_reply_escalation_flow(agent_env, support_db) -> None:
    """转人工轮：工具标记 → 话术落库 → ai→escalated（reason 留痕）；后续轮拒答。"""
    conv = await store.create_conversation()
    agent_env(
        [
            _tool_call("escalate_to_human", {"reason": "客户投诉进度缓慢"}, "c1"),
            AIMessage(content="已为您转接人工客服，请保持会话开启。"),
        ]
    )

    text = await reply(conv.id, "我要投诉！赶紧转人工")

    assert text == "已为您转接人工客服，请保持会话开启。"
    loaded = await store.get_conversation(conv.id)
    assert loaded.status == store.CONV_ESCALATED
    assert loaded.escalated_reason == "客户投诉进度缓慢"
    msgs = await store.list_messages(conv.id)
    assert [(m.role, m.content) for m in msgs] == [
        (store.ROLE_USER, "我要投诉！赶紧转人工"),
        # 转接话术仍在 AI 时间线（escalated 后 assistant 停答不追溯本轮）
        (store.ROLE_ASSISTANT, text),
    ]
    with pytest.raises(store.SupportStateError):
        await reply(conv.id, "还在吗")


async def test_reply_missing_conversation(agent_env) -> None:
    """会话不存在：LookupError。"""
    with pytest.raises(LookupError):
        await reply("nope", "你好")


def test_replay_messages_mapping() -> None:
    """历史映射：user→Human / assistant→AI，时序保持。"""
    msgs = [
        SupportMessage(conversation_id="c", role="user", content="q1"),
        SupportMessage(conversation_id="c", role="assistant", content="a1"),
        SupportMessage(conversation_id="c", role="user", content="q2"),
    ]
    replay = _replay_messages(msgs)
    assert [type(m) for m in replay] == [HumanMessage, AIMessage, HumanMessage]
    assert [m.content for m in replay] == ["q1", "a1", "q2"]


# ---------- 工具单测 ----------


async def _seed_case(factory) -> str:
    """种子：补件挂起中的医疗案（含 interrupted 回执与决定书草稿）。"""
    async with factory() as s:
        case = Case(
            id="CASE-2026-0001",
            user_id="u-1",
            policy_no="POL-2025-0001",
            case_type="medical",
            status="supplement_pending",
            claimed_amount=Decimal("15800.00"),
            incident_date=dt.date(2026, 8, 10),
            incident_description="急性阑尾炎住院",
        )
        s.add(case)
        s.add(
            CaseJob(
                case_id=case.id,
                action="run",
                payload={},
                status="succeeded",
                outcome="interrupted",
                interrupt_payload={
                    "kind": "supplement",
                    "reason": "缺发票",
                    "missing": ["invoice"],
                },
                attempt=1,
                max_attempts=3,
                run_after=dt.datetime(2026, 8, 10, 9, 0),
            )
        )
        s.add(
            DecisionDocument(
                case_id=case.id,
                version=1,
                title="理赔决定书",
                body="……",
                conclusion="approved",
                approved_amount=Decimal("4640.00"),
                issued_by="auto",
            )
        )
        s.add(CaseEvent(case_id=case.id, kind="routing", stage=None, seq=1, payload={}))
        s.add(
            CaseEvent(
                case_id=case.id, kind="stage_result", stage="material_review", seq=2, payload={}
            )
        )
        await s.commit()
    return case.id


async def test_case_status_query_tool(support_db) -> None:
    """进度查询：同口径投影（挂起信息/决定书/近事件），与 B02 详情一致口径。"""
    case_id = await _seed_case(support_db)
    result = await CaseStatusQueryTool().ainvoke({"case_id": case_id})

    assert result["success"] is True
    assert result["status"] == "supplement_pending"
    assert result["case_type"] == "medical"
    assert result["claimed_amount"] == "15800.00"
    assert result["human"] == {
        "kind": "supplement",
        "reason": "缺发票",
        "missing": ["invoice"],
    }
    assert result["decision_conclusion"] == "approved"
    assert result["decision_approved_amount"] == "4640.00"
    assert result["decision_issued"] is False
    assert [e["stage"] for e in result["recent_events"]] == [None, "material_review"]


async def test_case_status_query_missing(support_db) -> None:
    """案件不存在：业务失败（success=False），LLM 引导核对编号。"""
    result = await CaseStatusQueryTool().ainvoke({"case_id": "CASE-9999-0000"})
    assert result["success"] is False
    assert "未找到案件" in result["error_message"]


async def test_claim_draft_link_tool() -> None:
    """预填链接：全参/最简/非法险种/非法日期/非法金额。"""
    tool = ClaimDraftLinkTool()

    full = await tool.ainvoke(
        {
            "case_type": "auto",
            "incident_date": "2026-09-01",
            "claimed_amount": 8000.0,
            "description": "追尾事故，前保险杠维修",
        }
    )
    assert full["success"] is True
    assert full["url"].startswith("/?case_type=auto")
    assert "incident_date=2026-09-01" in full["url"]
    assert "claimed_amount=8000.00" in full["url"]
    assert "description=" in full["url"]

    minimal = await tool.ainvoke({"case_type": "medical"})
    assert minimal["success"] is True
    assert minimal["url"] == "/?case_type=medical"

    bad_line = await tool.ainvoke({"case_type": "dental"})
    assert bad_line["success"] is False
    assert "未知险种" in bad_line["error_message"]

    bad_date = await tool.ainvoke({"case_type": "medical", "incident_date": "2026/09/01"})
    assert bad_date["success"] is False
    assert "YYYY-MM-DD" in bad_date["error_message"]

    bad_amount = await tool.ainvoke({"case_type": "medical", "claimed_amount": -5})
    assert bad_amount["success"] is False


async def test_escalate_tool_marker() -> None:
    """转人工工具：标记式——只返回受理提示，不改会话状态（流转在 reply 层）。"""
    result = await EscalateToHumanTool().ainvoke({"reason": "测试"})
    assert result["success"] is True
    assert "hint" in result
