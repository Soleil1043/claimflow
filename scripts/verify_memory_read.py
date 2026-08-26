"""T035 验收脚本：长期记忆读注入（真实 LLM + 真实 BGE-M3 + 完整主图，dev profile）。

验收点：
1. 检索注入：新会话首轮"我上次问的那张保单"按 user_id 检索历史记忆命中
2. 跨会话上下文连贯：会话 B（同一用户新会话）完整主图流程的回答正确引用
   历史会话 A 的保单号 POL-2025-0001 与预估赔付金额
3. 无历史用户零影响：其他用户同问题检索空直跳，回答不引用该保单
"""

from __future__ import annotations

import asyncio
import uuid

from langchain_core.messages import AIMessage, HumanMessage
from qdrant_client import models

from app.core.config import settings
from services.memory.long_term import (
    format_memory_context,
    search_memories,
    summarize_conversation,
    write_memory,
)
from services.rag.qdrant_client import get_qdrant_client

USER_ID = "demo-user-002"
OTHER_USER_ID = "demo-user-other"

# 会话 A：张伟保单理赔（与 data/mock 预置数据一致）
SESSION_A_MESSAGES: list = [
    HumanMessage(content="我做了急性阑尾炎手术，保单 POL-2025-0001，住院花了15800元能赔多少？"),
    AIMessage(
        content="张伟您好，保单 POL-2025-0001（安心医疗旗舰版）生效中，免赔额 10,000 元、赔付比例 80%。"
        "本次住院费用 15,800 元预估赔付 4,640 元，最终以理赔审核结果为准。"
    ),
]

QUESTION = "我上次问的那张保单，最后说能赔多少来着？"


async def _cleanup(client) -> None:  # noqa: ANN001
    """清理本脚本演示用户的历史数据。"""
    collection = settings.qdrant_memory_collection
    if await client.collection_exists(collection):
        for uid in (USER_ID, OTHER_USER_ID):
            await client.delete(
                collection_name=collection,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(key="user_id", match=models.MatchValue(value=uid))
                        ]
                    )
                ),
            )


async def _ask_graph(graph, memory_context: str) -> str:  # noqa: ANN001
    """模拟 A06 首轮：注入记忆跑完整主图（intent → 分流 → … → 合规）。"""
    thread_id = f"verify-t035-{uuid.uuid4().hex[:8]}"
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content=QUESTION)],
            "conversation_id": thread_id,
            "memory_context": memory_context,
            "intent": None,
            "task_plan": [],
            "current_step": 0,
            "shared_data": {},
            "agent_steps": [],
            "tool_trace": [],
            "compliance_result": None,
            "compliance_rounds": 0,
            "final_answer": "",
            "need_human_intervention": False,
            "intervention_reason": None,
        },
        config={"configurable": {"thread_id": thread_id}},
    )
    return result.get("final_answer") or ""


async def main() -> None:
    # 0. 会话 A 落记忆（T034 写路径，真实 LLM 摘要）
    client = get_qdrant_client()
    await _cleanup(client)
    record = await summarize_conversation(
        SESSION_A_MESSAGES, conversation_id="demo-t035-session-a", user_id=USER_ID
    )
    await write_memory(record)
    print(f"[会话 A 记忆已写入] {record.summary}")

    # 1. 检索验证：新会话首轮问句按 user_id 检索命中
    hits = await search_memories(QUESTION, USER_ID)
    assert hits, "本人历史记忆检索未命中"
    memory_context = format_memory_context(hits)
    print(f"\n=== 1. 检索注入 === 命中 {len(hits)} 条（score={[round(h.score, 3) for h in hits]}）")
    print(f"[注入文本] {memory_context[:100]}...")

    # 2. 跨会话端到端：完整主图回答引用历史
    import tools.claim  # noqa: F401 注册工具
    import tools.compliance  # noqa: F401
    import tools.medical  # noqa: F401
    from services.memory.short_term import get_checkpoint_manager
    from tools.executor import ToolExecutor
    from tools.registry import get_default_registry
    from workflows.main_graph import build_main_graph

    checkpointer = await get_checkpoint_manager().start()
    graph = build_main_graph(
        executor=ToolExecutor(get_default_registry()), checkpointer=checkpointer
    )

    print("\n=== 2. 跨会话上下文（同一用户新会话，完整主图） ===")
    answer = await _ask_graph(graph, memory_context)
    print(f"[回答] {answer}")
    assert "POL-2025-0001" in answer, "回答未引用历史会话的保单号"
    assert ("4,640" in answer) or ("4640" in answer), "回答未引用历史会话的预估赔付金额"

    # 3. 无历史用户零影响：检索空直跳，回答不引用该保单
    other_hits = await search_memories(QUESTION, OTHER_USER_ID)
    assert other_hits == [], "无历史用户检索应直跳"
    print(f"\n=== 3. 无历史用户 === 检索 {len(other_hits)} 条（直跳，不注入）")
    other_answer = await _ask_graph(graph, "")
    print(f"[回答] {other_answer[:120]}...")
    assert "POL-2025-0001" not in other_answer, "无历史用户回答不应引用他人保单"

    print("\nT035 验收通过：检索注入 ✓ / 跨会话引用历史 ✓ / 无历史零影响 ✓")


asyncio.run(main())
