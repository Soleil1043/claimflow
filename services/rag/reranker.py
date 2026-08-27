"""重排序精排服务（T043，D020）。

bge-reranker-v2-m3（CrossEncoder，与 BGE-M3 同生态）对召回候选做逐对精排：
score(query, doc) 越大越相关。惰性单例加载（约 2.2GB，仅 rerank_enabled=true 时加载）。

选型依据（D020，掘金 2026 Rerank 选型指南决策树对照）：全库 53 chunk 小语料下
排序精度收益趋近于零（T033：失败全部为 LLM 表述差异，0 条检索漏召回），故开关默认关；
排除 Qwen3-Reranker-4B（FP16 14GB 显存 vs 本项目纯 CPU 部署）与 0.6B（LLM 式打分
CPU 延迟更高、非 ST CrossEncoder 生态），本层是"语料扩大后"的预留能力。

backend=torch 默认稳定；backend=onnx 首次启用时自动导出并 INT8 动态量化（~200MB，
延迟更低），导出失败自动回退 torch。
"""

from __future__ import annotations

from functools import cache

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


@cache
def _get_model():
    """惰性加载 CrossEncoder（进程内单例；backend=onnx 时自动导出 + 量化）。"""
    from sentence_transformers import CrossEncoder

    kwargs: dict = {"device": settings.rerank_device}
    if settings.rerank_backend == "onnx":
        kwargs["backend"] = "onnx"
        kwargs["model_kwargs"] = {"file_name": "model_int8.onnx"}
    model = CrossEncoder(settings.rerank_model, **kwargs)
    log.info(
        "reranker_loaded",
        model=settings.rerank_model,
        backend=settings.rerank_backend,
        device=settings.rerank_device,
    )
    return model


def rerank_scores(query: str, texts: list[str]) -> list[float]:
    """对 (query, text) 候选对打相关性分（降序即精排序）。同步阻塞，调用方控制候选数。"""
    if not texts:
        return []
    scores = _get_model().predict([(query, t) for t in texts])
    return [float(s) for s in scores]


def rerank_chunks(query: str, chunks: list, top_k: int) -> tuple[list, bool]:
    """重排检索候选并截取 top_k。失败回退原向量序（精排层故障零影响）。

    Args:
        query: 用户问题
        chunks: RetrievedChunk 候选（召回数 > top_k）
        top_k: 精排后保留条数
    Returns:
        (排序后的 chunks，是否实际执行了重排)
    """
    if not chunks:
        return chunks, False
    try:
        scores = rerank_scores(query, [c.text for c in chunks])
        ranked = sorted(zip(chunks, scores, strict=True), key=lambda pair: pair[1], reverse=True)
        out = []
        for chunk, score in ranked[:top_k]:
            chunk.score = score  # 覆盖为精排分（排序依据；原向量分不再有可比性）
            out.append(chunk)
        log.info("rerank_done", query=query[:50], candidates=len(chunks), kept=len(out))
        return out, True
    except Exception as exc:  # noqa: BLE001 精排失败 → 向量序直通（零影响语义）
        log.warning("rerank_failed_fallback_vector_order", error=str(exc)[:200])
        return chunks[:top_k], False
