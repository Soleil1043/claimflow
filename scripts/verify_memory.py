"""T034 验收脚本：长期记忆写路径（真实 LLM + 真实 BGE-M3 + dev Qdrant local mode）。

验收点：
1. 摘要质量抽验：3 轮真实感理赔对话 → LLM 摘要 + 关键实体（人工检查输出）
2. 向量化入库：写入独立 collection，payload 含 user_id（隔离键）
3. 幂等：同一会话重复写 → point 数不变（确定性 id upsert 覆盖）
4. user_id 隔离检索：按 user_id filter 向量检索命中本人记忆（T035 读路径依据）
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, HumanMessage
from qdrant_client import models

from app.core.config import settings
from services.memory.long_term import (
    memory_point_id,
    summarize_conversation,
    write_memory,
)
from services.rag.embedder import embed_query
from services.rag.qdrant_client import get_qdrant_client

# 张伟理赔场景（与 data/mock 预置数据一致）
USER_ID = "demo-user-001"
# 固定演示会话 id：脚本重跑覆盖同一条记忆（幂等演示的一部分）
CONVERSATION_ID = "demo-t034-verification"
MESSAGES: list = [
    HumanMessage(content="我做了急性阑尾炎手术，保单 POL-2025-0001，住院花了15800元能赔多少？"),
    AIMessage(
        content="张伟您好，保单 POL-2025-0001（安心医疗旗舰版）生效中，免赔额 10,000 元、赔付比例 80%。"
        "本次住院费用 15,800 元预估赔付 4,640 元（(15,800-10,000)×80%），最终以理赔审核结果为准。"
    ),
    HumanMessage(content="这个病有等待期问题吗？"),
    AIMessage(
        content="急性阑尾炎（ICD-10 K35）属于等待期 30 天规则覆盖范围，您的保单已过等待期，不影响本次理赔。"
    ),
    HumanMessage(content="理赔需要准备什么材料？"),
    AIMessage(
        content="住院理赔需准备：诊断证明、住院病历、费用清单原件、发票原件、银行卡信息。可通过线上或柜面提交。"
    ),
]


async def main() -> None:
    # 清理本脚本历次运行的演示数据（按 user_id 定点清除，不影响其他记忆）
    client = get_qdrant_client()
    collection = settings.qdrant_memory_collection
    if await client.collection_exists(collection):
        await client.delete(
            collection_name=collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(key="user_id", match=models.MatchValue(value=USER_ID))
                    ]
                )
            ),
        )

    # 1. 摘要生成（真实 LLM）
    record = await summarize_conversation(
        MESSAGES, conversation_id=CONVERSATION_ID, user_id=USER_ID
    )
    print("=== 1. 摘要与关键实体（质量抽验） ===")
    print(f"[来源] {record.source} | [轮数] {record.turn_count}")
    print(f"[摘要] {record.summary}")
    print(f"[实体] {record.entities}")
    assert record.source == "llm", "摘要走了兜底路径，请检查 LLM 配置"
    assert "POL-2025-0001" in record.entities["policy_nos"], "保单号实体缺失"
    assert any("阑尾炎" in d for d in record.entities["diagnoses"]), "诊断实体缺失"
    assert 15800.0 in record.entities["amounts"], "费用金额实体缺失"

    # 2. 写入独立 collection
    await write_memory(record)
    assert await client.collection_exists(collection), "记忆 collection 未创建"
    point = (
        await client.retrieve(
            collection_name=collection, ids=[memory_point_id(CONVERSATION_ID)], with_payload=True
        )
    )[0]
    print("\n=== 2. 入库验证 ===")
    print(f"[collection] {collection} | [point_id] {point.id}")
    print(f"[payload.user_id] {point.payload['user_id']}（隔离键）")
    assert point.payload["user_id"] == USER_ID

    # 3. 幂等：重复写不新增
    count_before = (await client.count(collection, exact=True)).count
    record.summary = record.summary + "（更新后摘要）"
    await write_memory(record)
    count_after = (await client.count(collection, exact=True)).count
    print(f"\n=== 3. 幂等验证 === 重复写前 {count_before} 条 → 写后 {count_after} 条")
    assert count_before == count_after == 1, "重复会话产生了重复条目"

    # 4. user_id 隔离检索（模拟 T035 读路径：按 user_id filter 向量检索）
    def _user_filter(value: str) -> models.Filter:
        return models.Filter(
            must=[models.FieldCondition(key="user_id", match=models.MatchValue(value=value))]
        )

    vector = embed_query("我上次问的那张保单能赔多少来着")
    hits = (
        await client.query_points(
            collection_name=collection,
            query=vector,
            limit=1,
            query_filter=_user_filter(USER_ID),
            with_payload=True,
        )
    ).points
    print("\n=== 4. 隔离检索验证 ===")
    print("[查询] 我上次问的那张保单能赔多少来着")
    print(f"[命中 score] {hits[0].score:.4f}")
    print(f"[命中摘要] {hits[0].payload['summary'][:80]}...")
    assert hits and hits[0].payload["user_id"] == USER_ID, "按 user_id 过滤检索未命中"

    # 反向验证：其他用户 filter 检索不到
    other = (
        await client.query_points(
            collection_name=collection,
            query=vector,
            limit=1,
            query_filter=_user_filter("someone-else"),
            with_payload=True,
        )
    ).points
    assert not other, "其他用户检索到了本人记忆（隔离失效）"
    print("[反向验证] 其他 user_id 检索 0 命中（隔离正确）")

    print("\nT034 验收通过：摘要质量 ✓ / 向量化入库 ✓ / 幂等 ✓ / user_id 隔离 ✓")


asyncio.run(main())
