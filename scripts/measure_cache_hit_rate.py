"""真实 DeepSeek 调用验证 Prompt Caching 命中率（T162 配套）。

与 probe_deepseek_cache_usage.py（mock 探针）互补：本脚本用真实 API 打两轮
相同大前缀的请求，验证三件事：
1. DeepSeek 真实返回 usage 里带 prompt_cache_hit_tokens / prompt_cache_miss_tokens
2. 这两个字段在 langchain ainvoke 链路上能否被 _extract_cache_usage 提取到
3. 二次调用（相同前缀）命中率是否显著高于首次 —— 即缓存是否真的生效

跑法：uv run python scripts/measure_cache_hit_rate.py [--rounds 3]
需要 .env 里的 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from services.llm.client import get_chat_model
from services.observability.llm_metrics import _extract_cache_usage, _model_name

# 模拟 skill 作业规程规模的稳定前缀（DeepSeek 硬盘缓存按前缀命中，长前缀更易观察）
STABLE_PREFIX = (
    "你是保险理赔核赔助手。严格遵守以下作业规程：\n"
    + "\n".join(f"{i}. 规程条款 {i}：核对保单有效性、金额、诊断与材料一致性。" for i in range(1, 41))
    + "\n请仅依据以下案件材料作答。\n"
)
CASE_TAIL = "\n\n案件材料：住院发票金额 12000 元，诊断为急性阑尾炎，已投保百万医疗险。请给出核赔结论。"


async def one_round(model: Any, round_idx: int) -> tuple[int | None, int | None, int | None]:
    """打一轮请求，返回 (hit, miss, prompt_tokens)。"""
    messages = [
        {"role": "system", "content": STABLE_PREFIX},
        {"role": "user", "content": CASE_TAIL + f"\n（本轮标记 round={round_idx}）"},
    ]
    resp = await model.ainvoke(messages)
    hit, miss = _extract_cache_usage(resp)
    usage = getattr(resp, "usage_metadata", None) or {}
    return hit, miss, usage.get("input_tokens")


async def main() -> None:
    parser = argparse.ArgumentParser(description="DeepSeek Prompt Caching 真实命中率测量")
    parser.add_argument("--rounds", type=int, default=3, help="打几轮（首轮冷启动，后续命中缓存）")
    args = parser.parse_args()

    model = get_chat_model()
    name = _model_name(model)
    print(f"模型：{name}")
    print(f"稳定前缀长度：{len(STABLE_PREFIX)} 字符")
    print()

    rows: list[tuple[int, int | None, int | None, int | None]] = []
    for i in range(1, args.rounds + 1):
        hit, miss, prompt = await one_round(model, i)
        rows.append((i, hit, miss, prompt))
        rate = (hit / (hit + miss)) if (hit is not None and miss is not None and (hit + miss) > 0) else None
        print(
            f"round {i}: hit={hit}  miss={miss}  input_tokens={prompt}  "
            f"命中率={f'{rate:.1%}' if rate is not None else 'N/A'}"
        )

    got = [(h, m) for _, h, m, _ in rows if h is not None or m is not None]
    print()
    if not got:
        print("结论：所有响应均未提取到缓存字段 —— DeepSeek 未返回非标字段，或提取链路失效。")
        sys.exit(1)
    tot_hit = sum(h or 0 for h, _ in got)
    tot_miss = sum(m or 0 for _, m in got)
    tot = tot_hit + tot_miss
    print(f"结论：{len(got)}/{len(rows)} 轮拿到缓存字段；整体命中 {tot_hit}/{tot} = {tot_hit / tot:.1%}" if tot else "结论：拿到字段但 token 为 0")
    print("（首轮通常为冷启动 miss，后续轮次才是真实稳态命中率）")


if __name__ == "__main__":
    asyncio.run(main())
