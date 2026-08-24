"""F03 验收脚本：意图分类真实 LLM 准确率（20 条测试集，≥90% 通过）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from nodes.intent import classify_intent

CASES_FILE = Path(__file__).resolve().parent.parent / "data" / "mock" / "intent_test_cases.json"


async def main() -> None:
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]

    correct = 0
    wrong: list[tuple[str, str, str]] = []
    fallbacks = 0
    for case in cases:
        result = await classify_intent(case["text"])
        if result.fallback:
            fallbacks += 1
        if result.intent == case["expected"]:
            correct += 1
        else:
            wrong.append((case["text"], case["expected"], result.intent))

    total = len(cases)
    accuracy = correct / total
    print(f"准确率: {correct}/{total} = {accuracy:.0%}")
    print(f"关键词兜底次数: {fallbacks}")
    for text, expected, got in wrong:
        print(f"  [误] {text} | 期望 {expected} 实际 {got}")
    assert accuracy >= 0.9, f"准确率 {accuracy:.0%} 低于 90% 验收线"
    print("F03 验收通过（≥90%）")


asyncio.run(main())
