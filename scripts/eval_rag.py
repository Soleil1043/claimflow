"""T161 RAG 评测运行器（责任认定阶段口径，D075）。

对 rag_qa_liability.json 的 24 对 QA：
1. search_kb top-8 检索 → gold chunk 名次 → Recall@1/2/4/8 + MRR + top_k 敏感度
2. top-4 窗口 → 按责任认定口径由片段作答（deepseek）→ RAGAS 四指标（自实现，
   算法口径对齐官方定义，见 evals/rag_metrics.py）
3. 报告落 evals/reports/t161_rag_eval.{json,md}：逐题表 + 分类汇总 + 失败归因

前置：向量索引与 data/kb_docs 一致——`uv run python -m services.rag.ingest`。
用法：
    uv run python -m scripts.eval_rag [--limit N] [--concurrency 4]
退出码：0=完成（指标本身不设门，T161 是证据采集非门禁）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.rag_metrics import (  # noqa: E402
    RAGAS_WINDOW,
    RETRIEVE_TOP_K,
    answer_from_contexts,
    answer_relevancy,
    compute_recall_metrics,
    context_precision,
    context_recall,
    faithfulness,
    gold_rank,
)
from evals.schemas import RagQADataset  # noqa: E402

QA_PATH = ROOT / "evals" / "datasets" / "rag_qa_liability.json"
REPORT_JSON = ROOT / "evals" / "reports" / "t161_rag_eval.json"
REPORT_MD = ROOT / "evals" / "reports" / "t161_rag_eval.md"


async def _eval_one(model, qa, sem: asyncio.Semaphore) -> dict:
    async with sem:
        from services.rag.retriever import search_kb

        started = time.perf_counter()
        chunks = await search_kb(qa.query, top_k=RETRIEVE_TOP_K)
        rank = gold_rank(chunks, qa)
        top = chunks[:RAGAS_WINDOW]
        answer = await answer_from_contexts(model, qa.query, top)
        cp = await context_precision(model, qa.query, top)
        cr = await context_recall(model, qa.gold_answer, top)
        fa = await faithfulness(model, answer, top)
        ar = await answer_relevancy(model, qa.query, answer)
        return {
            "id": qa.id,
            "category": qa.category,
            "query": qa.query,
            "gold_source_file": qa.gold_source_file,
            "gold_rank": rank,
            "retrieved_titles": [f"{c.source_file}::{c.title}" for c in top],
            "answer": answer,
            "ragas": {
                "context_precision": cp,
                "context_recall": cr,
                "faithfulness": fa,
                "answer_relevancy": ar,
            },
            "duration_s": round(time.perf_counter() - started, 2),
        }


def _mean(rows: list[dict], path: str) -> float:
    vals = [r for row in rows if (r := row["ragas"].get(path)) is not None]
    return round(sum(vals) / len(vals), 4) if vals else 0.0


def _write_md(rows: list[dict], recall: dict[str, float], report: dict) -> None:
    lines = [
        "# T161 责任认定 RAG 评测报告（Recall@K + RAGAS 四指标）\n",
        f"> 数据集：rag_qa_liability.json（{len(rows)} 对，责任认定高频问法）；",
        f"检索：BGE-M3 + Qdrant（top-{RETRIEVE_TOP_K}）；生成/判分：deepseek-flash；",
        "RAGAS 四指标自实现（算法口径对齐官方定义，理由见 decisions.md D075）。\n",
        "## 总览\n",
        "| 指标 | 数值 |",
        "|---|---|",
    ]
    for k in ("recall_at_1", "recall_at_2", "recall_at_4", "recall_at_8"):
        lines.append(f"| {k} | {recall[k]:.2%} |")
    lines.append(f"| MRR | {recall['mrr']:.4f} |")
    for name, label in (
        ("context_precision", "Context Precision"),
        ("context_recall", "Context Recall"),
        ("faithfulness", "Faithfulness"),
        ("answer_relevancy", "Answer Relevancy"),
    ):
        lines.append(f"| RAGAS {label}（mean） | {_mean(rows, name):.4f} |")

    lines.append("\n## 分类汇总\n")
    lines.append("| category | n | recall@4 | mean CP | mean CR | mean FA | mean AR |")
    lines.append("|---|---|---|---|---|---|---|")
    cats = sorted({r["category"] for r in rows})
    for cat in cats:
        sub = [r for r in rows if r["category"] == cat]
        r4 = sum(1 for r in sub if r["gold_rank"] and r["gold_rank"] <= 4) / len(sub)
        lines.append(
            f"| {cat} | {len(sub)} | {r4:.0%} | {_mean(sub, 'context_precision'):.4f} "
            f"| {_mean(sub, 'context_recall'):.4f} | {_mean(sub, 'faithfulness'):.4f} "
            f"| {_mean(sub, 'answer_relevancy'):.4f} |"
        )

    lines.append("\n## 逐题明细\n")
    lines.append("| id | cat | gold_rank | CP | CR | FA | AR | query |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        g = r["ragas"]
        lines.append(
            f"| {r['id']} | {r['category']} | {r['gold_rank'] or '×'} | {g['context_precision']:.2f} "
            f"| {g['context_recall']:.2f} | {g['faithfulness']:.2f} | {g['answer_relevancy']:.2f} "
            f"| {r['query'][:30]} |"
        )

    # 失败归因以 top-4（责任认定 Agent 的实际检索窗口）为准；gold 未进 top-8 的
    # 才是检索彻底失手——top-4 外但 top-8 内的多为"多文档同答案"的判定偏严
    misses = [r for r in rows if r["gold_rank"] is None or r["gold_rank"] > RAGAS_WINDOW]
    hard_miss = [r for r in rows if r["gold_rank"] is None]
    lines.append(
        f"\n## 失败归因（gold 未进 top-{RAGAS_WINDOW}：{len(misses)} 题；"
        f"未进 top-{RETRIEVE_TOP_K}：{len(hard_miss)} 题）\n"
    )
    lines.append(
        "判定偏严说明：知识库多份文档可回答同一问题（如潜水免责同时出现在免责汇总与"
        "意外险规则），gold 钉在单一文档，跨文档命中会计为 miss。\n"
    )
    for r in misses:
        lines.append(f"- **{r['id']}**（{r['category']}）`{r['query']}`")
        lines.append(
            f"  - 期望来源：`{r['gold_source_file']}`；实际 top-4："
            + "；".join(t for t in r["retrieved_titles"][:3])
        )

    r4, r8 = recall["recall_at_4"], recall["recall_at_8"]
    lines.append("\n## top_k 敏感度与改进项\n")
    lines.append(
        f"- recall@4={r4:.0%} vs recall@8={r8:.0%}"
        f"（{'扩大窗口有增益' if r8 > r4 else '扩窗无增益，瓶颈在召回排序'}）"
    )
    lines.append(
        "- 跨小节推理题（等待期×险种对照、目录×产品线）是主要失分类别，"
        "改进方向：分块策略携带文档级摘要前缀 / reranker 阈值调优 / 图检索补充"
    )
    lines.append(
        "- 免责类问法同义改写（'能赔吗' vs '免责'）依赖条款片段标题词，"
        "可在 ingest 侧为 ## 标题加同义关键词桥接\n"
    )

    lines.append("## 指标口径备注\n")
    lines.append("- gold 判定：chunk.source_file 相等且 gold_marker 为 chunk 文本逐字子串")
    lines.append(
        "- context_precision/recall、faithfulness 为 LLM 判分（deepseek-flash），"
        "存在判分方差；answer_relevancy 为嵌入余弦（确定性）"
    )
    lines.append(
        f"- RAGAS 观察窗口 top-{RAGAS_WINDOW}（与责任认定 Agent claim_rule_rag 的 top_k=4 对齐）"
    )
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main_async(limit: int | None, concurrency: int) -> int:
    dataset = RagQADataset.model_validate(json.loads(QA_PATH.read_text(encoding="utf-8")))
    cases = dataset.cases[:limit] if limit else dataset.cases

    from services.llm.client import get_chat_model

    model = get_chat_model()
    sem = asyncio.Semaphore(concurrency)
    rows = list(await asyncio.gather(*(_eval_one(model, qa, sem) for qa in cases)))

    ranks = [r["gold_rank"] for r in rows]
    recall = compute_recall_metrics(ranks, ks=(1, 2, 4, RETRIEVE_TOP_K))
    report = {
        "task": "T161 责任认定 RAG 评测（D075）",
        "model": "deepseek-flash",
        "embedder": "BAAI/bge-m3",
        "total": len(rows),
        "recall": recall,
        "ragas_mean": {
            "context_precision": _mean(rows, "context_precision"),
            "context_recall": _mean(rows, "context_recall"),
            "faithfulness": _mean(rows, "faithfulness"),
            "answer_relevancy": _mean(rows, "answer_relevancy"),
        },
        "duration_s_total": round(sum(r["duration_s"] for r in rows), 1),
        "rows": rows,
    }
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_md(rows, recall, report)

    print(f"\n{'=' * 60}")
    print(f"RAG 评测（{len(rows)} 对，top-{RETRIEVE_TOP_K} 检索）")
    print(f"{'=' * 60}")
    print(
        f"  Recall@1={recall['recall_at_1']:.0%} @2={recall['recall_at_2']:.0%} "
        f"@4={recall['recall_at_4']:.0%} @8={recall['recall_at_8']:.0%} | MRR={recall['mrr']:.4f}"
    )
    rm = report["ragas_mean"]
    print(
        f"  RAGAS: CP={rm['context_precision']:.4f} CR={rm['context_recall']:.4f} "
        f"FA={rm['faithfulness']:.4f} AR={rm['answer_relevancy']:.4f}"
    )
    print(f"报告 → {REPORT_JSON.relative_to(ROOT)} / {REPORT_MD.relative_to(ROOT)}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="T161 RAG 评测运行器")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 对")
    parser.add_argument("--concurrency", type=int, default=4, help="评测对并发数")
    args = parser.parse_args()
    sys.exit(asyncio.run(main_async(args.limit, args.concurrency)))


if __name__ == "__main__":
    main()
