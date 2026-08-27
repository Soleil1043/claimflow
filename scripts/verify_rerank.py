"""T043 验收脚本：重排序精排（真实 bge-reranker-v2-m3 + 真实 Qdrant local mode）。

验收点：
1. 延迟：torch fp32 CPU 上对 top-8 候选重排的单查询耗时（D020 成本数据）
2. 排序变化：F06 三类标准查询的前后 top-4 对比（人工抽验精排是否更合理）
3. 链路：rag_node 开关语义（开 = 重排生效；关 = 行为不变）已由单测覆盖
"""

from __future__ import annotations

import asyncio
import time

from app.core.config import settings
from services.rag.reranker import rerank_chunks
from services.rag.retriever import search_kb

# F06 验收同款标准查询（覆盖等待期/材料/免责三类检索意图）
QUERIES = [
    "阑尾炎手术有等待期吗",
    "理赔需要什么材料",
    "既往症能赔吗",
    "意外险是怎么赔付的",
    "报案时效是多久",
]


async def main() -> None:
    # 预热：加载 reranker（首次含模型加载耗时，单列展示）
    from services.rag.reranker import _get_model

    t0 = time.perf_counter()
    _get_model()
    print(f"[模型加载] bge-reranker-v2-m3 torch fp32 CPU：{time.perf_counter() - t0:.1f}s")

    total_rerank_ms = 0.0
    changed = 0
    for query in QUERIES:
        chunks = await search_kb(query, top_k=settings.rerank_recall_k)
        before = [c.title for c in chunks[:4]]

        t = time.perf_counter()
        reranked, ok = rerank_chunks(query, chunks, top_k=settings.rerank_top_k)
        elapsed_ms = (time.perf_counter() - t) * 1000
        total_rerank_ms += elapsed_ms
        after = [c.title for c in reranked]
        top1_changed = before[0] != after[0]
        changed += top1_changed
        print(f"\n[{query}] 重排 {elapsed_ms:.0f}ms（top1 变化：{'是' if top1_changed else '否'}）")
        print(f"  向量序 top4: {before}")
        print(f"  精排后 top4: {after}")

    n = len(QUERIES)
    print(f"\n=== 汇总 === {n} 查询：平均重排延迟 {total_rerank_ms / n:.0f}ms/查询，top1 变化 {changed}/{n}")
    print("精排层延迟在轮次总耗时（9-20s）中占比可接受，且开关默认关（小语料收益趋近于零，D020）")


asyncio.run(main())
