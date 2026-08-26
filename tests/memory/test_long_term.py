"""长期记忆写路径 + 读注入测试（T034/T035）。

策略同 tests/rag/test_retriever.py：mock embedder（固定 4 维向量）+ 临时 Qdrant local mode
+ 脚本化 LLM；覆盖摘要生成（LLM 主路径/非法输出/异常兜底）、确定性实体提取、
幂等写入（确定性 point id upsert）、user_id payload 隔离、轮数阈值触发、旁路容错，
以及 T035 读路径（user_id filter 检索 / min_score 噪声过滤 / 拼装截断 / 零影响直跳）。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError
from qdrant_client import models

import services.memory.long_term as lt
import services.rag.qdrant_client as qdrant_module
from services.memory.long_term import (
    MemoryRecord,
    count_user_turns,
    extract_entities_deterministic,
    format_memory_context,
    format_messages_for_summary,
    maybe_write_memory,
    memory_point_id,
    search_memories,
    summarize_conversation,
    write_memory,
)

# 合法 LLM 摘要响应（实体金额故意给字符串形式，验证归一化）
_LLM_SUMMARY_JSON = (
    '{"summary": "用户咨询急性阑尾炎手术理赔，涉及保单与住院费用，助手给出预估赔付结论。", '
    '"entities": {"policy_nos": ["POL-2025-0001"], "diagnoses": ["急性阑尾炎"], "amounts": ["15800"]}}'
)


class _FakeModel:
    """脚本化 LLM：依次返回预设内容（Exception 则抛出）。"""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)

    async def ainvoke(self, messages: Any, config: Any = None) -> Any:
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return AIMessage(content=item)


@pytest.fixture()
async def memory_env(tmp_path, monkeypatch):
    """记忆写路径环境：临时 Qdrant + 固定向量 + 开关打开 + N=3。"""
    monkeypatch.setattr(lt.settings, "memory_enabled", True)
    monkeypatch.setattr(lt.settings, "memory_summary_every_n_turns", 3)
    monkeypatch.setattr(lt.settings, "qdrant_memory_collection", "memory_test")
    monkeypatch.setattr(lt, "EMBEDDING_DIM", 4)
    monkeypatch.setattr(lt, "embed_texts", lambda texts: [[0.1, 0.2, 0.3, 0.4] for _ in texts])
    fake_client = qdrant_module.AsyncQdrantClient(path=str(tmp_path / "qdrant"))
    monkeypatch.setattr(lt, "get_qdrant_client", lambda: fake_client)
    yield fake_client
    await fake_client.close()


# ---------- 纯函数 ----------


def test_extract_entities_deterministic() -> None:
    """正则提取：保单号去重、金额千分位归一化。"""
    text = "保单 POL-2025-0001 住院花了 15,800 元，免赔 10,000 元，另一张 POL-2026-0005 重复 POL-2025-0001"
    ents = extract_entities_deterministic(text)
    assert ents["policy_nos"] == ["POL-2025-0001", "POL-2026-0005"]
    assert ents["diagnoses"] == []
    assert 15800.0 in ents["amounts"]
    assert 10000.0 in ents["amounts"]


def test_extract_entities_empty() -> None:
    """无实体文本：三类全空。"""
    ents = extract_entities_deterministic("你好，我想咨询一下理赔流程")
    assert ents == {"policy_nos": [], "diagnoses": [], "amounts": []}


def test_count_and_format_filter_noise() -> None:
    """用户轮数统计 + 空 content（ReAct 中间步）与 ToolMessage 过滤。"""
    msgs: list[Any] = [
        HumanMessage(content="保单 POL-2025-0001 能赔多少"),
        AIMessage(content=""),  # 纯 tool_calls 中间步
        ToolMessage(content="tool_raw_output", tool_call_id="x"),
        AIMessage(content="预估赔付 4,640 元"),
        HumanMessage(content="免赔额是多少"),
        AIMessage(content="免赔额 10,000 元"),
    ]
    assert count_user_turns(msgs) == 2
    text = format_messages_for_summary(msgs)
    assert "用户：保单" in text
    assert "助手：预估" in text
    assert "tool_raw_output" not in text


def test_memory_point_id_deterministic() -> None:
    """确定性 point id：同会话一致、异会话不同、合法 UUID。"""
    cid = str(uuid.uuid4())
    assert memory_point_id(cid) == memory_point_id(cid)
    assert memory_point_id(cid) != memory_point_id(str(uuid.uuid4()))
    uuid.UUID(memory_point_id(cid))


# ---------- 摘要生成 ----------


async def test_summarize_llm_path(monkeypatch) -> None:
    """LLM 主路径：摘要 + 实体（字符串金额归一化为 float）。"""
    monkeypatch.setattr(
        lt, "get_chat_model", lambda temperature=0.0: _FakeModel([_LLM_SUMMARY_JSON])
    )
    msgs: list[Any] = [
        HumanMessage(content="我做了急性阑尾炎手术，保单 POL-2025-0001，花了15800元能赔多少"),
        AIMessage(content="预估赔付 4,640 元"),
    ]
    record = await summarize_conversation(msgs, conversation_id="c1", user_id="u1")
    assert record.source == "llm"
    assert record.summary.startswith("用户咨询")
    assert record.entities["policy_nos"] == ["POL-2025-0001"]
    assert record.entities["diagnoses"] == ["急性阑尾炎"]
    assert record.entities["amounts"] == [15800.0]
    assert record.turn_count == 1
    assert record.updated_at


async def test_summarize_invalid_json_fallback(monkeypatch) -> None:
    """LLM 非法输出：降级兜底摘要，但实体正则仍提取。"""
    monkeypatch.setattr(
        lt, "get_chat_model", lambda temperature=0.0: _FakeModel(["抱歉我不是JSON"])
    )
    msgs: list[Any] = [
        HumanMessage(content="保单 POL-2025-0001 住院花了15800元"),
        AIMessage(content="预估赔付 4,640 元"),
    ]
    record = await summarize_conversation(msgs, conversation_id="c1", user_id="u1")
    assert record.source == "fallback"
    assert record.summary.startswith("【兜底摘要】")
    assert record.entities["policy_nos"] == ["POL-2025-0001"]
    assert 15800.0 in record.entities["amounts"]


async def test_summarize_llm_exception_fallback(monkeypatch) -> None:
    """LLM 异常：不抛错，兜底摘要仍生成。"""
    monkeypatch.setattr(
        lt, "get_chat_model", lambda temperature=0.0: _FakeModel([RuntimeError("api down")])
    )
    record = await summarize_conversation(
        [HumanMessage(content="你好")], conversation_id="c1", user_id="u1"
    )
    assert record.source == "fallback"
    assert "你好" in record.summary


async def test_summarize_empty_messages() -> None:
    """空消息列表：兜底摘要标空会话，不抛错。"""
    record = await summarize_conversation([], conversation_id="c1", user_id="u1")
    assert record.source == "fallback"
    assert record.turn_count == 0


# ---------- 写入与幂等 ----------


def _record(**overrides: Any) -> MemoryRecord:
    base: dict[str, Any] = {
        "conversation_id": "c1",
        "user_id": "u1",
        "summary": "测试摘要",
        "entities": {"policy_nos": ["POL-2025-0001"], "diagnoses": [], "amounts": [15800.0]},
        "turn_count": 3,
        "updated_at": "2026-08-26T00:00:00",
        "source": "llm",
    }
    base.update(overrides)
    return MemoryRecord(**base)


async def test_write_creates_collection_with_payload(memory_env) -> None:
    """首次写入：自动建 collection，payload 含 user_id/summary/entities/turn_count。"""
    await write_memory(_record())
    assert await memory_env.collection_exists("memory_test")
    points = await memory_env.retrieve(
        collection_name="memory_test", ids=[memory_point_id("c1")], with_payload=True
    )
    payload = points[0].payload
    assert payload["user_id"] == "u1"
    assert payload["conversation_id"] == "c1"
    assert payload["summary"] == "测试摘要"
    assert payload["entities"]["policy_nos"] == ["POL-2025-0001"]
    assert payload["turn_count"] == 3


async def test_write_idempotent_overwrites(memory_env) -> None:
    """幂等：同一会话重复写 → point 数仍为 1，payload 为最新内容（upsert 覆盖）。"""
    await write_memory(_record(summary="第一版", turn_count=3))
    await write_memory(_record(summary="第二版", turn_count=6))
    info = await memory_env.count("memory_test", exact=True)
    assert info.count == 1
    points = await memory_env.retrieve(collection_name="memory_test", ids=[memory_point_id("c1")])
    assert points[0].payload["summary"] == "第二版"
    assert points[0].payload["turn_count"] == 6


async def test_write_user_isolation(memory_env) -> None:
    """用户隔离：两个用户各自会话独立成点，payload user_id 区分（T035 filter 依据）。"""
    await write_memory(_record(conversation_id="c1", user_id="u1", summary="用户1记忆"))
    await write_memory(_record(conversation_id="c2", user_id="u2", summary="用户2记忆"))
    info = await memory_env.count("memory_test", exact=True)
    assert info.count == 2
    p1 = (await memory_env.retrieve(collection_name="memory_test", ids=[memory_point_id("c1")]))[0]
    p2 = (await memory_env.retrieve(collection_name="memory_test", ids=[memory_point_id("c2")]))[0]
    assert p1.payload["user_id"] == "u1"
    assert p2.payload["user_id"] == "u2"


def test_memory_record_validation() -> None:
    """MemoryRecord 缺必填字段：校验拒绝（schema 防呆）。"""
    with pytest.raises(ValidationError):
        MemoryRecord(conversation_id="c1")  # type: ignore[call-arg]


# ---------- 触发入口（A06 出口语义） ----------


def _msgs(*turns: tuple[str, str]) -> list[Any]:
    return [
        m for pair in turns for m in (HumanMessage(content=pair[0]), AIMessage(content=pair[1]))
    ]


async def test_maybe_write_triggers_every_n(memory_env, monkeypatch) -> None:
    """N=3：第 1/2 轮不触发，第 3 轮触发并写入一条。"""
    monkeypatch.setattr(
        lt, "get_chat_model", lambda temperature=0.0: _FakeModel([_LLM_SUMMARY_JSON])
    )
    assert (
        await maybe_write_memory(conversation_id="c1", user_id="u1", messages=_msgs(("问1", "答1")))
        is False
    )
    assert (
        await maybe_write_memory(
            conversation_id="c1", user_id="u1", messages=_msgs(("问1", "答1"), ("问2", "答2"))
        )
        is False
    )
    assert (
        await maybe_write_memory(
            conversation_id="c1",
            user_id="u1",
            messages=_msgs(("问1", "答1"), ("问2", "答2"), ("问3", "答3")),
        )
        is True
    )
    info = await memory_env.count("memory_test", exact=True)
    assert info.count == 1


async def test_maybe_write_force_on_terminal_state(memory_env, monkeypatch) -> None:
    """转人工终态：不满 N 轮也强制写快照（force=True）。"""
    monkeypatch.setattr(
        lt, "get_chat_model", lambda temperature=0.0: _FakeModel([_LLM_SUMMARY_JSON])
    )
    assert (
        await maybe_write_memory(
            conversation_id="c1", user_id="u1", messages=_msgs(("问1", "答1")), force=True
        )
        is True
    )


async def test_maybe_write_disabled(memory_env) -> None:
    """开关关闭：不写、不建 collection。"""
    import services.memory.long_term as long_term_module

    original = long_term_module.settings.memory_enabled
    long_term_module.settings.memory_enabled = False
    try:
        msgs = [HumanMessage(content=f"问{i}") for i in range(3)]
        assert await maybe_write_memory(conversation_id="c1", user_id="u1", messages=msgs) is False
        assert not await memory_env.collection_exists("memory_test")
    finally:
        long_term_module.settings.memory_enabled = original


async def test_maybe_write_zero_turns(memory_env, monkeypatch) -> None:
    """无用户消息（0 轮）：不触发。"""
    monkeypatch.setattr(
        lt, "get_chat_model", lambda temperature=0.0: _FakeModel([_LLM_SUMMARY_JSON])
    )
    assert (
        await maybe_write_memory(
            conversation_id="c1", user_id="u1", messages=[AIMessage(content="仅助手")]
        )
        is False
    )


async def test_maybe_write_failure_not_fatal(memory_env, monkeypatch) -> None:
    """写入层故障（Qdrant 宕机）：吞错返回 False，不向调用方抛（旁路语义）。"""
    monkeypatch.setattr(
        lt, "get_chat_model", lambda temperature=0.0: _FakeModel([_LLM_SUMMARY_JSON])
    )

    def broken_client() -> Any:
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(lt, "get_qdrant_client", broken_client)
    msgs = [HumanMessage(content=f"问{i}") for i in range(3)]
    assert await maybe_write_memory(conversation_id="c1", user_id="u1", messages=msgs) is False


# ---------- 读注入路径（T035） ----------

# 定向向量：查询含"保单"→ 与会话 A 同向（score 1），含"材料"→ 与会话 B 同向
_VEC_POLICY = [1.0, 0.0, 0.0, 0.0]
_VEC_MATERIAL = [0.0, 1.0, 0.0, 0.0]


async def _seed_memories(client: Any) -> None:
    """灌 3 条记忆：u1 两条（保单/材料主题正交向量）+ u2 一条（与 u1 保单同向）。"""
    await client.create_collection(
        collection_name="memory_test",
        vectors_config=models.VectorParams(size=4, distance=models.Distance.COSINE),
    )
    points = [
        models.PointStruct(
            id=memory_point_id("conv-a"),
            vector=_VEC_POLICY,
            payload={
                "user_id": "u1",
                "conversation_id": "conv-a",
                "summary": "用户咨询保单 POL-2025-0001 阑尾炎理赔，预估赔付 4640 元",
                "entities": {"policy_nos": ["POL-2025-0001"]},
                "updated_at": "2026-08-26T00:00:00",
            },
        ),
        models.PointStruct(
            id=memory_point_id("conv-b"),
            vector=_VEC_MATERIAL,
            payload={
                "user_id": "u1",
                "conversation_id": "conv-b",
                "summary": "用户咨询理赔材料清单",
                "entities": {},
                "updated_at": "2026-08-26T00:00:00",
            },
        ),
        # u2 的记忆与 u1 保单记忆同向：不用 filter 会被错误带回（隔离验证用）
        models.PointStruct(
            id=memory_point_id("conv-c"),
            vector=_VEC_POLICY,
            payload={
                "user_id": "u2",
                "conversation_id": "conv-c",
                "summary": "另一个用户的保单记忆",
                "entities": {},
                "updated_at": "2026-08-26T00:00:00",
            },
        ),
    ]
    await client.upsert(collection_name="memory_test", points=points)


@pytest.fixture()
async def search_env(tmp_path, monkeypatch):
    """读路径环境：临时 Qdrant + 定向 embed_query mock（已灌 3 条记忆）。"""
    monkeypatch.setattr(lt.settings, "memory_enabled", True)
    monkeypatch.setattr(lt.settings, "qdrant_memory_collection", "memory_test")
    monkeypatch.setattr(lt.settings, "memory_min_score", 0.4)
    monkeypatch.setattr(lt.settings, "memory_top_k", 2)

    def fake_embed_query(query: str) -> list[float]:
        return _VEC_MATERIAL if "材料" in query else _VEC_POLICY

    monkeypatch.setattr(lt, "embed_query", fake_embed_query)

    fake_client = qdrant_module.AsyncQdrantClient(path=str(tmp_path / "qdrant"))
    monkeypatch.setattr(lt, "get_qdrant_client", lambda: fake_client)
    await _seed_memories(fake_client)
    yield fake_client
    await fake_client.close()


async def test_search_user_isolation(search_env) -> None:
    """user_id filter 隔离：u1 检索只命中本人记忆（u2 同向点不带回）。"""
    hits = await search_memories("我上次问的那张保单", "u1")
    assert len(hits) == 1
    assert hits[0].conversation_id == "conv-a"
    assert "POL-2025-0001" in hits[0].summary
    assert hits[0].entities["policy_nos"] == ["POL-2025-0001"]
    assert hits[0].score >= 0.4


async def test_search_min_score_filters_noise(search_env) -> None:
    """min_score 过滤："材料"查询与保单记忆正交（score 0 < 0.4），仅材料记忆返回。"""
    hits = await search_memories("理赔需要什么材料", "u1")
    assert {h.conversation_id for h in hits} == {"conv-b"}


async def test_search_no_history_user_zero_impact(search_env, monkeypatch) -> None:
    """无历史用户：检索空直跳（不注入），不抛错。"""
    hits = await search_memories("我上次问的那张保单", "someone-else")
    assert hits == []
    assert format_memory_context(hits) == ""


async def test_search_disabled_returns_empty(search_env, monkeypatch) -> None:
    """开关关闭：不检索直接返回空。"""
    monkeypatch.setattr(lt.settings, "memory_enabled", False)
    assert await search_memories("任意", "u1") == []


async def test_search_collection_missing_returns_empty(tmp_path, monkeypatch) -> None:
    """collection 不存在（从未写过记忆）：空列表直跳。"""
    monkeypatch.setattr(lt.settings, "memory_enabled", True)
    monkeypatch.setattr(lt.settings, "qdrant_memory_collection", "memory_test")
    monkeypatch.setattr(lt, "embed_query", lambda _: _VEC_POLICY)
    fake_client = qdrant_module.AsyncQdrantClient(path=str(tmp_path / "empty"))
    monkeypatch.setattr(lt, "get_qdrant_client", lambda: fake_client)
    assert await search_memories("任意", "u1") == []
    await fake_client.close()


async def test_search_failure_not_fatal(search_env, monkeypatch) -> None:
    """Qdrant/embedding 故障：读路径吞错返回空（旁路零影响）。"""

    def broken_embed(query: str) -> list[float]:
        raise RuntimeError("embed down")

    monkeypatch.setattr(lt, "embed_query", broken_embed)
    assert await search_memories("任意", "u1") == []


def test_format_memory_context_joins_and_truncates() -> None:
    """拼装：多条合并为列表文本，总长截断（Token 预算控制）。"""
    from services.memory.long_term import MemoryHit

    hits = [
        MemoryHit(
            conversation_id="a",
            summary="摘要甲" * 100,
            entities={},
            score=0.9,
            updated_at="t",
        ),
        MemoryHit(conversation_id="b", summary="摘要乙", entities={}, score=0.8, updated_at="t"),
    ]
    text = format_memory_context(hits)
    assert text.startswith("- ")
    assert len(text) <= 1200
    short = format_memory_context(hits, max_chars=10)
    assert len(short) == 10
