"""门户客服 API 测试（T134）：建会话 / 状态 / 历史 / 发消息（ai 应答、escalated 停答、closed 409、LLM 故障 503）。"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
import services.support.agent as sa
from app.main import app
from services.db.models import Base
from services.db.session import dispose_engine
from services.support import store
from services.support.agent import reset_support_agent_cache
from tests.support.test_agent import _fake_tool_map


class ScriptedModel(GenericFakeChatModel):
    """脚本化模型：忽略工具绑定（按预排消息应答）。"""

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedModel:  # noqa: ARG002
        return self


class StreamableScriptedModel(ScriptedModel):
    """流式友好脚本模型（T180）：整段消息单 chunk 产出。

    GenericFakeChatModel 的 _stream 只按 content 切块——空 content + tool_calls
    的消息（转人工轮）会零 chunk，触发聚合层 "No generations found in stream"
    （真实模型流式恒带 tool_call_chunks，属替身局限而非业务缺陷）。此处整段
    单 chunk 并显式构造 tool_call_chunks，聚合后 tool_calls 完整保留。
    """

    def bind_tools(self, tools: Any, **kwargs: Any) -> StreamableScriptedModel:  # noqa: ARG002
        return self

    def _stream(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any):  # noqa: ARG002
        import json as _json

        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        result = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        message = result.generations[0].message
        chunk = AIMessageChunk(
            content=message.content,
            additional_kwargs=dict(message.additional_kwargs or {}),
            tool_call_chunks=[
                {
                    "name": tc["name"],
                    "args": _json.dumps(tc["args"], ensure_ascii=False),
                    "id": tc["id"],
                    "index": idx,
                    "type": "tool_call_chunk",
                }
                for idx, tc in enumerate(message.tool_calls or [])
            ],
            id=message.id,
        )
        if run_manager:
            run_manager.on_llm_new_token(str(message.content), chunk=chunk)
        yield ChatGenerationChunk(message=chunk)


class BoomModel(GenericFakeChatModel):
    """必然故障模型（LLM 不可用路径）。"""

    def bind_tools(self, tools: Any, **kwargs: Any) -> BoomModel:  # noqa: ARG002
        return self

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Any:
        raise RuntimeError("llm down")


def _tool_call_msg() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "escalate_to_human",
                "args": {"reason": "客户要求人工"},
                "id": "c1",
                "type": "tool_call",
            }
        ],
    )


@pytest.fixture()
async def client(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """文件库 + 脚本化模型客户端；脚本可按测试重设（set_script），agent 缓存复位。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'support_api_test.db').as_posix()}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module.swap_engine(engine, factory)

    script: list[AIMessage] = [AIMessage(content="您好，请问有什么可以帮您？")]

    def set_script(messages: list[AIMessage]) -> None:
        script.clear()
        script.extend(messages)
        reset_support_agent_cache()

    monkeypatch.setattr(
        sa, "get_chat_model", lambda temperature=0.1: StreamableScriptedModel(messages=iter(script))
    )
    monkeypatch.setattr(sa, "get_default_tool_map", _fake_tool_map)
    reset_support_agent_cache()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, set_script
    reset_support_agent_cache()
    await engine.dispose()
    await dispose_engine()


async def _create(ac: AsyncClient) -> str:
    resp = await ac.post("/api/v1/support/conversations")
    assert resp.status_code == 201
    return resp.json()["conversation_id"]


async def test_create_conversation(client) -> None:
    """建会话：201，uuid hex 主键，初始 ai。"""
    ac, _ = client
    resp = await ac.post("/api/v1/support/conversations")
    body = resp.json()
    assert resp.status_code == 201
    assert len(body["conversation_id"]) == 32
    assert body["status"] == "ai"
    assert body["created_at"]


async def test_roundtrip_status_and_history(client) -> None:
    """普通轮：发消息返回 AI 回复；状态/历史口径一致。"""
    ac, _ = client
    conv_id = await _create(ac)

    sent = await ac.post(
        f"/api/v1/support/conversations/{conv_id}/messages", json={"content": "你好"}
    )
    assert sent.status_code == 200
    assert sent.json() == {
        "status": "ai",
        "reply": "您好，请问有什么可以帮您？",
    }

    status = (await ac.get(f"/api/v1/support/conversations/{conv_id}")).json()
    assert status["status"] == "ai"
    assert status["escalated_reason"] is None

    history = (
        await ac.get(f"/api/v1/support/conversations/{conv_id}/messages")
    ).json()
    assert history["total"] == 2
    assert [(m["role"], m["content"]) for m in history["items"]] == [
        ("user", "你好"),
        ("assistant", "您好，请问有什么可以帮您？"),
    ]


async def test_escalated_stops_ai(client) -> None:
    """转人工轮：响应终态 escalated + 转接话术；此后发消息 reply=None（AI 停答）。"""
    ac, set_script = client
    conv_id = await _create(ac)
    set_script(
        [
            _tool_call_msg(),
            AIMessage(content="已为您转接人工客服，请保持会话开启。"),
        ]
    )

    escalated = await ac.post(
        f"/api/v1/support/conversations/{conv_id}/messages",
        json={"content": "给我转人工"},
    )
    assert escalated.status_code == 200
    assert escalated.json() == {
        "status": "escalated",
        "reply": "已为您转接人工客服，请保持会话开启。",
    }

    detail = (await ac.get(f"/api/v1/support/conversations/{conv_id}")).json()
    assert detail["status"] == "escalated"
    assert detail["escalated_reason"] == "客户要求人工"
    assert detail["escalated_at"]

    followup = await ac.post(
        f"/api/v1/support/conversations/{conv_id}/messages",
        json={"content": "好的，我等"},
    )
    assert followup.status_code == 200
    assert followup.json() == {"status": "escalated", "reply": None}

    history = (
        await ac.get(f"/api/v1/support/conversations/{conv_id}/messages")
    ).json()
    # AI 停答：escalated 后只有新增 user 消息，无 assistant
    assert [m["role"] for m in history["items"]] == ["user", "assistant", "user"]


async def test_send_message_not_found(client) -> None:
    ac, _ = client
    resp = await ac.post(
        "/api/v1/support/conversations/nope/messages", json={"content": "你好"}
    )
    assert resp.status_code == 404
    assert (await ac.get("/api/v1/support/conversations/nope")).status_code == 404
    assert (
        await ac.get("/api/v1/support/conversations/nope/messages")
    ).status_code == 404


async def test_closed_conflict(client) -> None:
    """closed 终态：发消息 409。"""
    ac, _ = client
    conv_id = await _create(ac)
    await store.close_conversation(conv_id)
    resp = await ac.post(
        f"/api/v1/support/conversations/{conv_id}/messages", json={"content": "在吗"}
    )
    assert resp.status_code == 409


async def test_llm_failure_503_message_persisted(client, monkeypatch) -> None:
    """LLM 故障：503，但用户消息已落库（时间线不丢，下一条重试）。"""
    ac, _ = client
    monkeypatch.setattr(sa, "get_chat_model", lambda temperature=0.1: BoomModel())
    reset_support_agent_cache()
    conv_id = await _create(ac)

    resp = await ac.post(
        f"/api/v1/support/conversations/{conv_id}/messages", json={"content": "你好"}
    )
    assert resp.status_code == 503

    history = (
        await ac.get(f"/api/v1/support/conversations/{conv_id}/messages")
    ).json()
    assert [(m["role"], m["content"]) for m in history["items"]] == [("user", "你好")]


async def test_tickets_full_flow(client) -> None:
    """坐席工单全流程：转人工入队（含最新消息预览）→ 坐席回复门户可见 → 备注关闭 → 队列清空详情留档。"""
    ac, set_script = client
    conv_id = await _create(ac)
    assert (await ac.get("/api/v1/support/tickets")).json()["total"] == 0

    set_script(
        [
            _tool_call_msg(),
            AIMessage(content="已为您转接人工客服，请稍候。"),
        ]
    )
    escalated = await ac.post(
        f"/api/v1/support/conversations/{conv_id}/messages", json={"content": "转人工"}
    )
    assert escalated.json()["status"] == "escalated"

    tickets = (await ac.get("/api/v1/support/tickets")).json()
    assert tickets["total"] == 1
    item = tickets["items"][0]
    assert item["conversation_id"] == conv_id
    assert item["escalated_reason"] == "客户要求人工"
    assert item["last_message"]["role"] == "assistant"
    assert item["last_message"]["content"] == "已为您转接人工客服，请稍候。"

    reply = await ac.post(
        f"/api/v1/support/tickets/{conv_id}/reply",
        json={"agent": "agent-01", "content": "您好，我是人工坐席，请描述您的问题"},
    )
    assert reply.status_code == 200
    assert reply.json()["status"] == "escalated"
    assert reply.json()["message"]["role"] == "agent"

    # 门户侧（同会话 messages 口径）可见坐席回复
    history = (
        await ac.get(f"/api/v1/support/conversations/{conv_id}/messages")
    ).json()
    assert history["items"][-1]["content"] == "您好，我是人工坐席，请描述您的问题"

    closed = await ac.post(
        f"/api/v1/support/tickets/{conv_id}/close",
        json={"agent": "agent-01", "note": "问题已在线解决，会话关闭。"},
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    assert closed.json()["closed_at"]

    # 队列清空；closed 详情仍可审计，关闭备注为最后一条坐席消息
    assert (await ac.get("/api/v1/support/tickets")).json()["total"] == 0
    detail = (await ac.get(f"/api/v1/support/tickets/{conv_id}")).json()
    assert detail["status"] == "closed"
    assert [m["role"] for m in detail["messages"]] == [
        "user",
        "assistant",
        "agent",
        "agent",
    ]
    assert detail["messages"][-1]["content"] == "问题已在线解决，会话关闭。"

    # 关闭后门户再发消息 409
    follow = await ac.post(
        f"/api/v1/support/conversations/{conv_id}/messages", json={"content": "x"}
    )
    assert follow.status_code == 409


async def test_ticket_actions_require_escalated(client) -> None:
    """ai 态会话不可坐席操作（409）；不存在 404。"""
    ac, _ = client
    conv_id = await _create(ac)
    reply = await ac.post(
        f"/api/v1/support/tickets/{conv_id}/reply",
        json={"agent": "a", "content": "x"},
    )
    assert reply.status_code == 409
    close = await ac.post(f"/api/v1/support/tickets/{conv_id}/close", json={"agent": "a"})
    assert close.status_code == 409
    assert (await ac.get("/api/v1/support/tickets/nope")).status_code == 404


# ===== SSE 流式发消息（T180，D085） =====


class TestStreamMessages:
    async def test_stream_tokens_and_done(self, client) -> None:
        """普通轮：200 + text/event-stream，token 与 done 事件齐备，时间线落 user+assistant。"""
        ac, _ = client
        conv_id = await _create(ac)
        resp = await ac.post(
            f"/api/v1/support/conversations/{conv_id}/messages/stream",
            json={"content": "你好"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = resp.text
        assert '"type": "token"' in body and "您好" in body  # 脚本模型全文一个 chunk
        assert '"type": "done"' in body and '"status": "ai"' in body

        hist = await ac.get(f"/api/v1/support/conversations/{conv_id}/messages")
        roles = [m["role"] for m in hist.json()["items"]]
        assert roles == ["user", "assistant"]

    async def test_stream_escalation_done_status_then_409(self, client) -> None:
        """转人工轮：done.status=escalated；此后流式端点 409（AI 停答无可流）。"""
        ac, set_script = client
        set_script([_tool_call_msg(), AIMessage(content="已为您转接人工客服，请保持会话开启。")])
        conv_id = await _create(ac)
        resp = await ac.post(
            f"/api/v1/support/conversations/{conv_id}/messages/stream",
            json={"content": "转人工"},
        )
        assert resp.status_code == 200
        assert '"status": "escalated"' in resp.text

        second = await ac.post(
            f"/api/v1/support/conversations/{conv_id}/messages/stream",
            json={"content": "还在吗"},
        )
        assert second.status_code == 409

    async def test_stream_llm_failure_error_event_user_persisted(
        self, client, monkeypatch
    ) -> None:
        """LLM 故障：error 事件（HTTP 已 200 开流）；用户消息已落库、assistant 不落（503 同语义）。"""
        ac, _ = client
        monkeypatch.setattr(sa, "get_chat_model", lambda temperature=0.1: BoomModel())
        reset_support_agent_cache()
        conv_id = await _create(ac)
        resp = await ac.post(
            f"/api/v1/support/conversations/{conv_id}/messages/stream",
            json={"content": "你好"},
        )
        assert resp.status_code == 200  # 开流后错误经事件传达
        assert '"type": "error"' in resp.text

        hist = await ac.get(f"/api/v1/support/conversations/{conv_id}/messages")
        roles = [m["role"] for m in hist.json()["items"]]
        assert roles == ["user"]  # assistant 未落，下一条消息重试不丢时间线

    async def test_stream_unknown_conversation_404(self, client) -> None:
        ac, _ = client
        resp = await ac.post(
            "/api/v1/support/conversations/nope/messages/stream",
            json={"content": "你好"},
        )
        assert resp.status_code == 404

    async def test_stream_closed_conversation_409(self, client) -> None:
        ac, _ = client
        conv_id = await _create(ac)
        await store.close_conversation(conv_id)
        resp = await ac.post(
            f"/api/v1/support/conversations/{conv_id}/messages/stream",
            json={"content": "你好"},
        )
        assert resp.status_code == 409


# ---------- 客服查案属主授权（T202，D088/C2） ----------

_GRANT_STAFF_KEYS = "alice:key-alice-123"


async def _seed_portal_case(case_id: str = "CASE-2026-0101") -> str:
    """种子：带门户令牌哈希的案件，返回明文令牌。"""
    import datetime as dt
    from decimal import Decimal

    from services.case_service import generate_access_token
    from services.db.models import Case

    token, token_hash = generate_access_token()
    async with session_module.get_session_factory()() as s:
        s.add(
            Case(
                id=case_id,
                user_id="u-grant",
                policy_no="POL-2025-0001",
                case_type="medical",
                status="closed",
                claimed_amount=Decimal("15800.00"),
                incident_date=dt.date(2026, 8, 10),
                incident_description="急性阑尾炎住院",
                access_token_hash=token_hash,
            )
        )
        await s.commit()
    return token


class TestCaseGrants:
    """授权端点：令牌校验与 REST 同源；不存在与令牌错同回 401（无枚举 oracle）。"""

    @pytest.fixture(autouse=True)
    def _secure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "staff_keys", _GRANT_STAFF_KEYS)

    async def test_valid_token_grants(self, client) -> None:
        ac, _ = client
        conv_id = await _create(ac)
        token = await _seed_portal_case()
        resp = await ac.post(
            f"/api/v1/support/conversations/{conv_id}/case-grants",
            json={"case_id": "CASE-2026-0101", "case_token": token},
        )
        assert resp.status_code == 201
        assert resp.json() == {"conversation_id": conv_id, "case_id": "CASE-2026-0101"}
        assert await store.case_granted(conv_id, "CASE-2026-0101") is True
        # 幂等：重复授权仍 201
        again = await ac.post(
            f"/api/v1/support/conversations/{conv_id}/case-grants",
            json={"case_id": "CASE-2026-0101", "case_token": token},
        )
        assert again.status_code == 201

    async def test_wrong_token_and_missing_case_same_401(self, client) -> None:
        ac, _ = client
        conv_id = await _create(ac)
        await _seed_portal_case()
        wrong = await ac.post(
            f"/api/v1/support/conversations/{conv_id}/case-grants",
            json={"case_id": "CASE-2026-0101", "case_token": "forged"},
        )
        missing = await ac.post(
            f"/api/v1/support/conversations/{conv_id}/case-grants",
            json={"case_id": "CASE-9999-0000", "case_token": "forged"},
        )
        assert wrong.status_code == missing.status_code == 401
        assert wrong.json() == missing.json()
        assert await store.case_granted(conv_id, "CASE-2026-0101") is False

    async def test_unknown_conversation_404(self, client) -> None:
        ac, _ = client
        token = await _seed_portal_case()
        resp = await ac.post(
            "/api/v1/support/conversations/nope/case-grants",
            json={"case_id": "CASE-2026-0101", "case_token": token},
        )
        assert resp.status_code == 404

    async def test_stream_path_granted_tool_reads_case(self, client, monkeypatch) -> None:
        """SSE 路径：授权后 AI 调 case_status_query 真实读库（作用域贯穿事件流）。"""
        import tools.claim.case_status as cs_module

        ac, set_script = client
        conv_id = await _create(ac)
        token = await _seed_portal_case()
        await ac.post(
            f"/api/v1/support/conversations/{conv_id}/case-grants",
            json={"case_id": "CASE-2026-0101", "case_token": token},
        )
        reads: list[str] = []
        real_snapshot = cs_module.case_progress_snapshot

        async def spy(cid: str):
            reads.append(cid)
            return await real_snapshot(cid)

        monkeypatch.setattr(cs_module, "case_progress_snapshot", spy)
        set_script(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "case_status_query",
                            "args": {"case_id": "CASE-2026-0101"},
                            "id": "q1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="您的案件已结案。"),
            ]
        )
        resp = await ac.post(
            f"/api/v1/support/conversations/{conv_id}/messages/stream",
            json={"content": "查一下 CASE-2026-0101"},
        )
        assert resp.status_code == 200
        assert '"type": "done"' in resp.text
        assert reads == ["CASE-2026-0101"]

    async def test_ungranted_conversation_cannot_read_case(self, client, monkeypatch) -> None:
        """匿名会话未授权：AI 调工具也读不到案件（C2 原始攻击路径）。"""
        import tools.claim.case_status as cs_module

        ac, set_script = client
        conv_id = await _create(ac)
        await _seed_portal_case()
        reads: list[str] = []
        monkeypatch.setattr(
            cs_module, "case_progress_snapshot", lambda cid: reads.append(cid)  # type: ignore[arg-type]
        )
        set_script(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "case_status_query",
                            "args": {"case_id": "CASE-2026-0101"},
                            "id": "q1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="请在案件详情页发起咨询。"),
            ]
        )
        resp = await ac.post(
            f"/api/v1/support/conversations/{conv_id}/messages",
            json={"content": "查一下 CASE-2026-0101"},
        )
        assert resp.status_code == 200
        assert reads == []
