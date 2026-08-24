"""F06 验收脚本：检索质量验证。"""

import asyncio

from services.rag.retriever import search_kb


async def main() -> None:
    for query in [
        "阑尾炎手术有等待期吗",
        "理赔需要什么材料",
        "既往症能赔吗",
    ]:
        print(f"\n=== 查询: {query} ===")
        chunks = await search_kb(query, top_k=3)
        for i, c in enumerate(chunks, 1):
            print(f"[{i}] score={c.score:.4f} | {c.title} ({c.category})")
            print(f"    {c.text[:120].replace(chr(10), ' ')}...")


asyncio.run(main())
