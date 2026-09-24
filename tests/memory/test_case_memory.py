"""申请人记忆（T100）测试：确定性渲染 / 终态钩子 / 检索过滤 / 幂等重建 + 治理（T138）。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

import services.memory.long_term as lt
from services.memory.case_memory import (
    case_memory_key,
    delete_case_memory,
    format_case_memories,
    put_case_memory,
    render_case_memory,
    search_case_memories,
    write_case_memory,
)

# ===== 渲染（纯函数） =====


def test_render_deterministic_fields() -> None:
    """终态事实 → 结构化档案；金额量化到分。"""
    record, embed = render_case_memory(
        case_id="CASE-2026-0001",
        user_id="u-1",
        case_type="medical",
        outcome="auto_issued",
        final_decision="approved",
        approved_amount=Decimal("4640"),
        reason=None,
        incident_date="2026-08-10",
    )
    assert record.kind == "case"
    assert record.approved_amount == "4640.00"
    assert record.reason is None
    assert "核定金额 4640.00 元" in embed
    assert "auto_issued" not in embed or "结论 approved" in embed


def test_render_key_is_deterministic_per_case() -> None:
    """key 由 case_id 派生：重跑/重建 upsert 覆盖（幂等）。"""
    assert case_memory_key("CASE-2026-0001") == case_memory_key("CASE-2026-0001")
    assert case_memory_key("CASE-2026-0001") != case_memory_key("CASE-2026-0002")


# ===== 终态钩子（InMemoryStore + 定向向量桩，不加载 BGE-M3） =====


@pytest.fixture()
def memory_store(monkeypatch):
    """开启记忆 + 桩嵌入（"拒赔"向 / 其他向正交），Store 单例复位兜底。"""

    def fake_embed(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "拒赔" in t or "rejected" in t else [0.0, 1.0] for t in texts]

    monkeypatch.setattr(lt, "_embed_for_store", fake_embed)
    monkeypatch.setattr(lt.settings, "memory_enabled", True)
    lt.reset_memory_store()
    yield
    lt.reset_memory_store()


def _state(**overrides) -> dict:
    base = {
        "case_id": "CASE-2026-0001",
        "user_id": "u-1",
        "case_type": "medical",
        "final_decision": "approved",
        "approved_amount": Decimal("4640.00"),
        "incident_date": "2026-08-10",
    }
    base.update(overrides)
    return base


async def test_write_then_search_returns_record(memory_store) -> None:
    await write_case_memory(_state(), outcome="auto_issued")
    hits = await search_case_memories("u-1")
    assert len(hits) == 1
    assert hits[0].case_id == "CASE-2026-0001"
    assert hits[0].outcome == "auto_issued"


async def test_search_excludes_current_case(memory_store) -> None:
    """详情页口径：排除本案件，避免回声。"""
    await write_case_memory(_state(), outcome="auto_issued")
    hits = await search_case_memories("u-1", exclude_case_id="CASE-2026-0001")
    assert hits == []


async def test_search_filters_non_case_kind(memory_store) -> None:
    """会话记忆等其他种类不进申请人档案视图（kind 过滤）。"""
    store = lt.get_memory_store()
    await store.aput(("memory", "u-1"), "conv-1", {"kind": "conversation", "summary": "咨询", "embed_text": "咨询"})
    hits = await search_case_memories("u-1")
    assert hits == []


async def test_write_disabled_is_noop(monkeypatch) -> None:
    """memory_enabled=False（默认测试口径）时零写入零异常。"""
    monkeypatch.setattr(lt.settings, "memory_enabled", False)
    lt.reset_memory_store()
    await write_case_memory(_state(), outcome="auto_issued")  # 不建 store、不抛错
    lt.reset_memory_store()


async def test_write_fail_open(memory_store, monkeypatch, caplog) -> None:
    """Store 异常 → 告警不抛错（核赔主流程零影响）。"""
    import services.memory.case_memory as cm

    async def boom(*a, **kw):
        raise RuntimeError("store down")

    monkeypatch.setattr(cm, "put_case_memory", boom)
    await write_case_memory(_state(), outcome="auto_issued")  # 吞异常


# ===== 展示格式 =====


def test_format_case_memories_lines() -> None:
    record, _ = render_case_memory(
        case_id="C1", user_id="u", case_type="medical", outcome="referred",
        final_decision="referred", reason="等待期内出险",
    )
    text = format_case_memories([record])
    assert "C1" in text and "等待期内出险" in text and "referred" in text


# ===== 治理：置信度门控 / TTL / 删除（T138，D058） =====


async def test_write_gated_below_confidence_floor(memory_store) -> None:
    """置信度门控：低于 floor 不入档（低置信 not_covered 档案源头拦断）。"""
    await write_case_memory(_state(), outcome="auto_issued", confidence=0.5)
    assert await search_case_memories("u-1") == []


async def test_confidence_persisted_and_default(memory_store) -> None:
    """置信度随档案落库；human 路径缺省 1.0（确定性事实）。"""
    await write_case_memory(_state(), outcome="auto_issued", confidence=0.85)
    record, _ = render_case_memory(
        case_id="C9", user_id="u-1", case_type="medical", outcome="closed"
    )
    assert record.confidence == 1.0

    hits = await search_case_memories("u-1")
    assert hits[0].confidence == 0.85


async def test_ttl_written_and_lazy_expiry(memory_store) -> None:
    """TTL：默认写入未来 expires_at；过期条目读取被滤除并惰性删除。"""
    await write_case_memory(_state(), outcome="auto_issued")
    hits = await search_case_memories("u-1")
    assert hits[0].expires_at is not None
    assert dt.datetime.fromisoformat(hits[0].expires_at) > dt.datetime.now()

    # 手工放入一条已过期档案（模拟时间流逝）
    expired, embed = render_case_memory(
        case_id="CASE-2026-0099", user_id="u-1", case_type="medical",
        outcome="auto_issued", incident_date="2026-01-01",
    )
    expired = expired.model_copy(
        update={"expires_at": (dt.datetime.now() - dt.timedelta(days=1)).isoformat(timespec="seconds")}
    )
    await put_case_memory(expired, embed)

    hits = await search_case_memories("u-1")
    assert [h.case_id for h in hits] == ["CASE-2026-0001"]  # 过期条目被滤除

    # 惰性清理：过期条目已从 Store 删除
    store = lt.get_memory_store()
    item = await store.aget(("memory", "u-1"), case_memory_key("CASE-2026-0099"))
    assert item is None


async def test_ttl_disabled_never_expires(memory_store, monkeypatch) -> None:
    """memory_ttl_days=0：不写 expires_at，永不惰性过期。"""
    monkeypatch.setattr(lt.settings, "memory_ttl_days", 0)
    record, embed = render_case_memory(
        case_id="C2", user_id="u-2", case_type="auto", outcome="auto_issued"
    )
    assert record.expires_at is None
    await put_case_memory(record, embed)
    assert len(await search_case_memories("u-2")) == 1


async def test_delete_case_memory(memory_store) -> None:
    """删除链路：存在→删除 True→档案消失；再删/不存在 False。"""
    await write_case_memory(_state(), outcome="auto_issued")
    assert await delete_case_memory("u-1", "CASE-2026-0001") is True
    assert await search_case_memories("u-1") == []
    assert await delete_case_memory("u-1", "CASE-2026-0001") is False
    assert await delete_case_memory("u-1", "CASE-NOT-EXIST") is False


# ===== 记忆种子终态语义（T107：verdict 判定单源后 missing 案不再自相矛盾） =====


async def test_seed_semantics_missing_case_referred(memory_store, monkeypatch) -> None:
    """转人工案（route != auto）记忆终态 = referred，与 outcome 不再矛盾。"""
    from types import SimpleNamespace

    from evals.adjudication_harness import seed_eval_memories as _seed_memories

    def _case(case_id, user_id, route, category, liability, amount):
        return SimpleNamespace(
            case_id=case_id, user_id=user_id, declared_case_type="medical",
            expected=SimpleNamespace(route=route, category=category, liability=liability,
                                     approved_amount=amount, note="缺件"),
            incident_date="2026-08-10",
        )

    cases = [
        _case("M1", "u-seed", "human", "missing", None, None),   # 旧代码"缺件"死分支 → approved 矛盾
        _case("A1", "u-seed", "auto", "normal", "covered", "100.00"),
    ]
    await _seed_memories(cases)
    hits = await search_case_memories("u-seed")
    by_id = {h.case_id: h for h in hits}
    assert by_id["M1"].outcome == "referred" and by_id["M1"].final_decision == "referred"
    assert by_id["A1"].outcome == "auto_issued" and by_id["A1"].final_decision == "approved"
