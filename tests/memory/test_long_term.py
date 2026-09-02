"""长期记忆写路径 + 读注入测试（T034/T035；T048 迁移 LangGraph Store）。

策略：mock embedder（文本内容 → 定向 4 维向量）+ InMemoryStore（官方 Store dev 后端，
dims=4 适配桩向量）+ 脚本化 LLM；覆盖摘要生成（LLM 主路径/非法输出/异常兜底）、
确定性实体提取、幂等写入（确定性 key upsert）、user_id 命名空间隔离、轮数阈值触发、
旁路容错，以及读路径（命名空间检索 / min_score 噪声过滤 / 拼装截断 / 零影响直跳）
与跨会话写读闭环（T048）。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.store.memory import InMemoryStore
from pydantic import ValidationError

import services.memory.long_term as lt
from services.memory.long_term import (
    MemoryRecord,
    count_user_turns,
    extract_entities_deterministic,
    format_memory_context,
    format_messages_for_summary,
    maybe_write_memory,
    memory_key,
    search_memories,
    summarize_conversation,
    write_memory,
)

# 合法 LLM 摘要响应（实体金额故意给字符串形式，验证归一化）
_LLM_SUMMARY_JSON = (
    '{"summary": "用户咨询急性阑尾炎手术理赔，涉及保单与住院费用，助手给出预估赔付结论。", '
    '"entities": {"policy_nos": ["POL-2025-0001"], "diagnoses": ["急性阑尾炎"], "amounts": ["15800"]}}'
)

# 定向向量：含"保单/POL"→ 保单向；含"材料"→ 材料向（正交，cosine=0）
_VEC_POLICY = [1.0, 0.0, 0.0, 0.0]
_VEC_MATERIAL = [0.0, 1.0, 0.0, 0.0]


class _FakeModel:
    """脚本化 LLM：依次返回预设内容（Exception 则抛出）。"""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)

    async def ainvoke(self, messages: Any, config: Any = None) -> Any:
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return AIMessage(content=item)


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """定向嵌入桩：文本含保单/POL → 保单向；含材料 → 材料向；默认材料向。"""
    vectors = []
    for t in texts:
        if "保单" in t or "POL" in t:
            vectors.append(_VEC_POLICY)
        else:
            vectors.append(_VEC_MATERIAL)
    return vectors


@pytest.fixture()
def store_env(monkeypatch):
    """记忆 Store 环境：InMemoryStore（dims=4 + 定向嵌入桩）+ 开关打开 + N=3。"""
    monkeypatch.setattr(lt.settings, "memory_enabled", True)
    monkeypatch.setattr(lt.settings, "memory_summary_every_n_turns", 3)
    monkeypatch.setattr(lt.settings, "memory_top_k", 2)
    monkeypatch.setattr(lt.settings, "memory_min_score", 0.4)
    monkeypatch.setattr(lt, "EMBEDDING_DIM", 4)
    monkeypatch.setattr(lt, "embed_texts", _fake_embed)
    store = InMemoryStore(
        index={"dims": 4, "embed": _fake_embed, "fields": ["embed_text"]}
    )
    monkeypatch.setattr(lt, "_memory_store", store)
    yield store


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


def test_memory_key_deterministic() -> None:
    """确定性 key：同会话一致、异会话不同、合法 UUID。"""
    cid = str(uuid.uuid4())
    assert memory_key(cid) == memory_key(cid)
    assert memory_key(cid) != memory_key(str(uuid.uuid4()))
    uuid.UUID(memory_key(cid))


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


# ---------- 写入与幂等（Store） ----------


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


async def test_write_stores_payload(store_env) -> None:
    """写入 Store：value 含 user_id/conversation_id/summary/entities/turn_count。"""
    await write_memory(_record())
    item = store_env.get(("memory", "u1"), memory_key("c1"))
    assert item is not None
    assert item.value["user_id"] == "u1"
    assert item.value["conversation_id"] == "c1"
    assert item.value["summary"] == "测试摘要"
    assert item.value["entities"]["policy_nos"] == ["POL-2025-0001"]
    assert item.value["turn_count"] == 3


async def test_write_idempotent_overwrites(store_env) -> None:
    """幂等：同一会话重复写 → 条目数仍为 1，value 为最新内容（key 覆盖）。"""
    await write_memory(_record(summary="第一版", turn_count=3))
    await write_memory(_record(summary="第二版", turn_count=6))
    items = store_env.search(("memory", "u1"))
    assert len(items) == 1
    assert items[0].value["summary"] == "第二版"
    assert items[0].value["turn_count"] == 6


async def test_write_user_isolation(store_env) -> None:
    """用户隔离：两个用户各自会话独立成条（namespace 隔离，T035 检索依据）。"""
    await write_memory(_record(conversation_id="c1", user_id="u1", summary="用户1记忆"))
    await write_memory(_record(conversation_id="c2", user_id="u2", summary="用户2记忆"))
    assert len(store_env.search(("memory", "u1"))) == 1
    assert len(store_env.search(("memory", "u2"))) == 1
    item1 = store_env.get(("memory", "u1"), memory_key("c1"))
    assert item1.value["user_id"] == "u1"


def test_memory_record_validation() -> None:
    """MemoryRecord 缺必填字段：校验拒绝（schema 防呆）。"""
    with pytest.raises(ValidationError):
        MemoryRecord(conversation_id="c1")  # type: ignore[call-arg]


# ---------- 触发入口（A06 出口语义） ----------


def _msgs(*turns: tuple[str, str]) -> list[Any]:
    return [
        m for pair in turns for m in (HumanMessage(content=pair[0]), AIMessage(content=pair[1]))
    ]


async def test_maybe_write_triggers_every_n(store_env, monkeypatch) -> None:
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
    assert len(store_env.search(("memory", "u1"))) == 1


async def test_maybe_write_force_on_terminal_state(store_env, monkeypatch) -> None:
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


async def test_maybe_write_disabled(store_env) -> None:
    """开关关闭：不写、不入 Store。"""
    import services.memory.long_term as long_term_module

    original = long_term_module.settings.memory_enabled
    long_term_module.settings.memory_enabled = False
    try:
        msgs = [HumanMessage(content=f"问{i}") for i in range(3)]
        assert await maybe_write_memory(conversation_id="c1", user_id="u1", messages=msgs) is False
        assert store_env.search(("memory", "u1")) == []
    finally:
        long_term_module.settings.memory_enabled = original


async def test_maybe_write_zero_turns(store_env, monkeypatch) -> None:
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


async def test_maybe_write_failure_not_fatal(store_env, monkeypatch) -> None:
    """写入层故障（嵌入函数宕机）：吞错返回 False，不向调用方抛（旁路语义）。"""
    monkeypatch.setattr(
        lt, "get_chat_model", lambda temperature=0.0: _FakeModel([_LLM_SUMMARY_JSON])
    )

    def broken_embed(texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embed down")

    monkeypatch.setattr(lt, "embed_texts", broken_embed)
    monkeypatch.setattr(lt, "_memory_store", None)  # 重建 Store 拾取坏嵌入
    msgs = [HumanMessage(content=f"问{i}") for i in range(3)]
    assert await maybe_write_memory(conversation_id="c1", user_id="u1", messages=msgs) is False


# ---------- 读注入路径（T035/T048） ----------


async def _seed_memories(store: InMemoryStore) -> None:
    """灌 3 条记忆：u1 两条（保单/材料主题正交向量）+ u2 一条（与 u1 保单同向）。"""
    seed = [
        (
            ("memory", "u1"),
            [
                (
                    memory_key("conv-a"),
                    {
                        "user_id": "u1",
                        "conversation_id": "conv-a",
                        "summary": "用户咨询保单 POL-2025-0001 阑尾炎理赔，预估赔付 4640 元",
                        "entities": {"policy_nos": ["POL-2025-0001"]},
                        "updated_at": "2026-08-26T00:00:00",
                        "embed_text": "保单 POL-2025-0001 阑尾炎理赔",
                    },
                ),
                (
                    memory_key("conv-b"),
                    {
                        "user_id": "u1",
                        "conversation_id": "conv-b",
                        "summary": "用户咨询理赔材料清单",
                        "entities": {},
                        "updated_at": "2026-08-26T00:00:00",
                        "embed_text": "理赔材料清单",
                    },
                ),
            ],
        ),
        (
            ("memory", "u2"),
            [
                (
                    memory_key("conv-c"),
                    {
                        "user_id": "u2",
                        "conversation_id": "conv-c",
                        "summary": "另一个用户的保单记忆",
                        "entities": {},
                        "updated_at": "2026-08-26T00:00:00",
                        "embed_text": "保单记忆（u2）",
                    },
                ),
            ],
        ),
    ]
    for ns, items in seed:
        for key, value in items:
            store.put(ns, key, value)


@pytest.fixture()
async def search_env(store_env):
    """读路径环境：已灌 3 条记忆（嵌入桩按文本内容定向）。"""
    await _seed_memories(store_env)
    return store_env


async def test_search_user_isolation(search_env) -> None:
    """user_id 命名空间隔离：u1 检索只命中本人记忆（u2 同向条目不带回）。"""
    hits = await search_memories("我上次问的那张保单", "u1")
    assert len(hits) == 1
    assert hits[0].conversation_id == "conv-a"
    assert "POL-2025-0001" in hits[0].summary
    assert hits[0].entities["policy_nos"] == ["POL-2025-0001"]
    assert hits[0].score >= 0.4


async def test_search_min_score_filters_noise(search_env) -> None:
    """min_score 过滤："材料"查询与保单记忆正交（score≈0 < 0.4），仅材料记忆返回。"""
    hits = await search_memories("理赔需要什么材料", "u1")
    assert {h.conversation_id for h in hits} == {"conv-b"}


async def test_search_no_history_user_zero_impact(search_env) -> None:
    """无历史用户：检索空直跳（不注入），不抛错。"""
    hits = await search_memories("我上次问的那张保单", "someone-else")
    assert hits == []
    assert format_memory_context(hits) == ""


async def test_search_disabled_returns_empty(search_env, monkeypatch) -> None:
    """开关关闭：不检索直接返回空。"""
    monkeypatch.setattr(lt.settings, "memory_enabled", False)
    assert await search_memories("任意", "u1") == []


async def test_search_failure_not_fatal(store_env, monkeypatch) -> None:
    """嵌入故障：读路径吞错返回空（旁路零影响）。"""

    def broken_embed(texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embed down")

    monkeypatch.setattr(lt, "embed_texts", broken_embed)
    monkeypatch.setattr(lt, "_memory_store", None)
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


# ---------- 跨会话写读闭环（T048 验收） ----------


async def test_write_then_search_cross_session_roundtrip(store_env, monkeypatch) -> None:
    """跨会话闭环：会话 A 写入记忆 → 同用户新会话 B 按语义检索命中同一保单。"""
    monkeypatch.setattr(
        lt, "get_chat_model", lambda temperature=0.0: _FakeModel([_LLM_SUMMARY_JSON])
    )
    # 会话 A：3 轮触发写入（LLM 摘要含 POL-2025-0001）
    assert (
        await maybe_write_memory(
            conversation_id="conv-a",
            user_id="u1",
            messages=_msgs(
                ("保单 POL-2025-0001 阑尾炎住院花了15800元能赔多少", "预估赔付 4640 元"),
                ("免赔额是多少", "免赔额 1 万元"),
                ("多久到账", "通常数个工作日"),
            ),
        )
        is True
    )

    # 会话 B（新会话）：首轮按语义检索命中会话 A 的记忆
    hits = await search_memories("我上次问的那张保单能赔多少", "u1")
    assert hits, "跨会话检索未命中会话 A 的记忆"
    assert hits[0].conversation_id == "conv-a"
    assert "POL-2025-0001" in hits[0].summary or "POL-2025-0001" in str(hits[0].entities)
    context = format_memory_context(hits)
    assert "POL-2025-0001" in context or "保单" in context
