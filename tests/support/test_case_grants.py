"""客服查案属主授权测试（T202，D088/C2）：工具属主断言 / 授权幂等 / 会话作用域传播。

C2 回归点：case_status_query 此前仅凭 case_id 读库，匿名客服会话可枚举全库案件。
修复后（staff_keys 配置的安全模式）只能查本会话已授权的案件；"不存在"与
"未授权"同一失败口径；客服 Agent 作用域外 fail-closed。
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import func, select

from app.core.config import settings
from services.db.models import SupportCaseGrant
from services.support import store
from services.support.agent import reply
from services.support.context import conversation_scope, current_conversation_id
from tests.support.test_agent import _seed_case, _tool_call, agent_env  # noqa: F401
from tools.claim.case_status import CaseStatusQueryTool

STAFF_KEYS = "alice:key-alice-123"


@pytest.fixture()
def secure_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置 staff_keys（prod 恒如此）：关闭 dev 开放态。"""
    monkeypatch.setattr(settings, "staff_keys", STAFF_KEYS)


async def test_no_conversation_scope_fail_closed(support_db, secure_mode) -> None:
    """客服 Agent 作用域外调用：即使案件存在也不吐快照。"""
    case_id = await _seed_case(support_db)
    result = await CaseStatusQueryTool().ainvoke({"case_id": case_id})
    assert result["success"] is False
    assert "status" not in result and "claimed_amount" not in result


async def test_ungranted_same_message_as_missing(support_db, secure_mode) -> None:
    """未授权与不存在同一口径（无枚举 oracle）：只有编号不同。"""
    case_id = await _seed_case(support_db)
    conv = await store.create_conversation()
    tool = CaseStatusQueryTool()
    with conversation_scope(conv.id):
        ungranted = await tool.ainvoke({"case_id": case_id})
        missing = await tool.ainvoke({"case_id": "CASE-9999-0000"})
    assert ungranted["success"] is False and missing["success"] is False
    assert ungranted.keys() == missing.keys()
    assert ungranted["error_message"].replace(case_id, "X") == missing[
        "error_message"
    ].replace("CASE-9999-0000", "X")


async def test_granted_case_returns_snapshot(support_db, secure_mode) -> None:
    """本会话已授权：返回进度快照；他会话未授权仍拒绝。"""
    case_id = await _seed_case(support_db)
    owner = await store.create_conversation()
    other = await store.create_conversation()
    await store.grant_case(owner.id, case_id)
    tool = CaseStatusQueryTool()

    with conversation_scope(owner.id):
        ok = await tool.ainvoke({"case_id": case_id})
    assert ok["success"] is True
    assert ok["status"] == "supplement_pending"

    with conversation_scope(other.id):
        denied = await tool.ainvoke({"case_id": case_id})
    assert denied["success"] is False


async def test_dev_open_mode_no_grant_needed(support_db) -> None:
    """dev 开放态（staff_keys 未配置）与 REST 同口径放行。"""
    assert not settings.staff_key_map
    case_id = await _seed_case(support_db)
    result = await CaseStatusQueryTool().ainvoke({"case_id": case_id})
    assert result["success"] is True


async def test_grant_case_idempotent(support_db) -> None:
    """重复授权同一案件只落一行。"""
    case_id = await _seed_case(support_db)
    conv = await store.create_conversation()
    await store.grant_case(conv.id, case_id)
    await store.grant_case(conv.id, case_id)
    async with support_db() as s:
        count = await s.scalar(
            select(func.count()).select_from(SupportCaseGrant).where(
                SupportCaseGrant.conversation_id == conv.id
            )
        )
    assert count == 1
    assert await store.case_granted(conv.id, case_id) is True
    assert await store.case_granted(conv.id, "CASE-9999-0000") is False


def test_conversation_scope_set_and_reset() -> None:
    """作用域内可见，退出后复位。"""
    assert current_conversation_id() is None
    with conversation_scope("conv-1"):
        assert current_conversation_id() == "conv-1"
    assert current_conversation_id() is None


@pytest.mark.parametrize("granted", [True, False])
async def test_scope_propagates_into_agent_tool_call(
    agent_env, support_db, secure_mode, monkeypatch, granted  # noqa: F811
) -> None:
    """reply() 作用域经 langgraph ToolNode 传播到工具：授权与否决定是否读库。

    快照读取只在属主断言通过后发生——spy 命中即证明工具在 Agent 内拿到了
    当前会话 id 且识别了授权行；未授权会话 spy 不得命中。
    """
    import tools.claim.case_status as cs_module

    case_id = await _seed_case(support_db)
    conv = await store.create_conversation()
    if granted:
        await store.grant_case(conv.id, case_id)

    reads: list[str] = []
    real_snapshot = cs_module.case_progress_snapshot

    async def spy(cid: str):
        reads.append(cid)
        return await real_snapshot(cid)

    monkeypatch.setattr(cs_module, "case_progress_snapshot", spy)
    agent_env(
        [
            _tool_call("case_status_query", {"case_id": case_id}, "t1"),
            AIMessage(content="已为您查询。"),
        ]
    )
    await reply(conv.id, f"查一下 {case_id}")

    assert reads == ([case_id] if granted else [])
    assert current_conversation_id() is None  # 作用域已复位
