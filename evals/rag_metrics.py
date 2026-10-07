"""RAG 质量评测度量（T161，D075）：Recall@K/MRR + RAGAS 四指标自实现。

自实现而非引入 ragas 包的理由见 decisions.md D075（huggingface-hub 跨大版本
漂移 + 依赖面扩大）；四指标算法口径对齐 RAGAS 官方定义（Es et al., 2023 /
docs.ragas.io）：

- context_precision：检索块对回答问题的有用性精确率——LLM 逐块判有用性 v_i，
  按平均精度 AP = Σ(precision@i × v_i) / Σv_i 计算
- context_recall：基准答案能否被检索块归因——基准答案拆主张，逐条判可归因比例
- faithfulness：生成答案的事实忠实度——答案拆主张，逐条判能否被检索块归因
- answer_relevancy：答案与问题的相关度——由答案生成反向问题，BGE-M3 嵌入与
  原问题余弦（官方算法，嵌入模型现成）

判召回（Recall@K/MRR）用 gold chunk 精确判定：source_file 相等且 gold_marker
为 chunk 文本子串（gold_marker 逐字校验由 tests/evals/test_rag_metrics.py 强制）。
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage

from evals.schemas import RagQACase
from services.rag.retriever import RetrievedChunk

# 默认检索深度与 RAGAS 观察窗口（top_k 敏感度对比由此展开）
RETRIEVE_TOP_K = 8
RAGAS_WINDOW = 4


# ---------- 判召回（纯函数，零 LLM） ----------


def gold_rank(chunks: list[RetrievedChunk], qa: RagQACase) -> int | None:
    """gold chunk 在检索结果中的名次（1 起）；未命中返回 None。"""
    for i, c in enumerate(chunks, 1):
        if c.source_file == qa.gold_source_file and qa.gold_marker in c.text:
            return i
    return None


def compute_recall_metrics(
    ranks: list[int | None], ks: tuple[int, ...] = (1, 2, 4, 8)
) -> dict[str, float]:
    """名次列表 → Recall@K + MRR（gold 未命中该 K 窗口即不计入分子）。"""
    n = len(ranks)
    out: dict[str, float] = {}
    for k in ks:
        hits = sum(1 for r in ranks if r is not None and r <= k)
        out[f"recall_at_{k}"] = round(hits / n, 4) if n else 0.0
    recip = [1.0 / r for r in ranks if r is not None]
    out["mrr"] = round(math.fsum(recip) / n, 4) if n else 0.0
    return out


def gold_chunk_in_source(qa: RagQACase, kb_dir: Any) -> bool:
    """gold_marker 必须逐字存在于源文档（测试用；kb_dir: Path）。"""
    text = (kb_dir / qa.gold_source_file).read_text(encoding="utf-8")
    return qa.gold_marker in text


# ---------- LLM 判分底座 ----------


def _parse_json_array(raw: str) -> list[Any]:
    """模型输出 → JSON 数组（容忍代码围栏与前后杂讯）。"""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError("未找到 JSON")
    data, _end = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(data, list):
        raise ValueError("期望 JSON 数组")
    return data


async def _ask_json(model: BaseChatModel, prompt: str) -> list[Any]:
    response = await model.ainvoke([HumanMessage(content=prompt)])
    return _parse_json_array(str(response.content))


async def _decompose_claims(model: BaseChatModel, text: str) -> list[str]:
    """文本 → 原子主张列表（faithfulness / context_recall 共用）。"""
    from services.llm.prompts import RAGAS_CLAIM_DECOMPOSITION_PROMPT

    if not text.strip():
        return []
    out = await _ask_json(model, RAGAS_CLAIM_DECOMPOSITION_PROMPT.format(text=text))
    return [str(x) for x in out if str(x).strip()]


async def _attribute_claims(
    model: BaseChatModel, claims: list[str], chunk_texts: list[str]
) -> list[int]:
    """逐条判主张能否被条款片段归因（1/0）。"""
    from services.llm.prompts import RAGAS_CLAIM_ATTRIBUTION_PROMPT

    if not claims:
        return []
    contexts = "\n\n".join(f"[片段{i + 1}] {t}" for i, t in enumerate(chunk_texts))
    claims_block = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(claims))
    prompt = RAGAS_CLAIM_ATTRIBUTION_PROMPT.format(contexts=contexts, claims=claims_block)
    votes = await _ask_json(model, prompt)
    return [1 if str(v).strip() in {"1", "true", "True"} else 0 for v in votes]


# ---------- RAGAS 四指标 ----------


async def context_precision(
    model: BaseChatModel, query: str, chunks: list[RetrievedChunk]
) -> float:
    """检索块有用性精确率（AP 口径，对齐 RAGAS context_precision）。"""
    from services.llm.prompts import RAGAS_CONTEXT_RELEVANCE_PROMPT

    if not chunks:
        return 0.0
    chunks_block = "\n\n".join(f"[片段{i + 1}] {c.text}" for i, c in enumerate(chunks))
    prompt = RAGAS_CONTEXT_RELEVANCE_PROMPT.format(query=query, chunks=chunks_block)
    votes = await _ask_json(model, prompt)
    rel_total = 0
    score = 0.0
    for i, v in enumerate(votes[: len(chunks)], 1):
        rel = 1 if str(v).strip() in {"1", "true", "True"} else 0
        rel_total += rel
        score += (rel_total / i) * rel
    return round(score / rel_total, 4) if rel_total else 0.0


async def context_recall(
    model: BaseChatModel, gold_answer: str, chunks: list[RetrievedChunk]
) -> float:
    """基准答案可归因比例（对齐 RAGAS context_recall 的 claim 归因口径）。"""
    claims = await _decompose_claims(model, gold_answer)
    votes = await _attribute_claims(model, claims, [c.text for c in chunks])
    return round(sum(votes) / len(votes), 4) if votes else 0.0


async def faithfulness(model: BaseChatModel, answer: str, chunks: list[RetrievedChunk]) -> float:
    """生成答案的主张支持率（对齐 RAGAS faithfulness）。"""
    claims = await _decompose_claims(model, answer)
    votes = await _attribute_claims(model, claims, [c.text for c in chunks])
    return round(sum(votes) / len(votes), 4) if votes else 0.0


async def answer_relevancy(model: BaseChatModel, query: str, answer: str) -> float:
    """反向问题嵌入余弦（对齐 RAGAS answer_relevancy；BGE-M3 已归一化）。"""
    from services.llm.prompts import RAGAS_REVERSE_QUESTION_PROMPT
    from services.rag.embedder import embed_query

    if not answer.strip():
        return 0.0
    questions = await _ask_json(model, RAGAS_REVERSE_QUESTION_PROMPT.format(answer=answer))
    if not questions:
        return 0.0
    q_vec = embed_query(query)
    r_vecs = embed_query([str(q) for q in questions][:3])
    sims = [math.fsum(a * b for a, b in zip(q_vec, rv, strict=True)) for rv in r_vecs]
    return round(sum(sims) / len(sims), 4)


async def answer_from_contexts(
    model: BaseChatModel, query: str, chunks: list[RetrievedChunk]
) -> str:
    """按责任认定口径由检索片段作答（RAGAS faithfulness 的生成侧输入）。"""
    from services.llm.prompts import RAG_QA_ANSWER_PROMPT

    contexts = "\n\n".join(f"[片段{i + 1}] {c.text}" for i, c in enumerate(chunks))
    response = await model.ainvoke(
        [HumanMessage(content=RAG_QA_ANSWER_PROMPT.format(contexts=contexts, query=query))]
    )
    return str(response.content).strip()
