"""BGE-M3 Embedding 服务（D003：本地 sentence-transformers）。

惰性单例加载模型（首次加载约 2GB，避免 import 时阻塞）；
1024 维向量，同时支持批量编码（入库）与单条编码（查询）。
"""

from __future__ import annotations

from functools import cache

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# BGE-M3 输出维度（Qdrant collection 向量维度需与此一致）
EMBEDDING_DIM = 1024


@cache
def _get_model():
    """惰性加载 sentence-transformers 模型（进程内单例）。"""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(settings.embedding_model, device=settings.embedding_device)
    log.info("embedding_model_loaded", model=settings.embedding_model, device=settings.embedding_device)
    return model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量编码（入库用）：返回与输入等长的向量列表。"""
    if not texts:
        return []
    vectors = _get_model().encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


def embed_query(text: str) -> list[float]:
    """单条编码（检索用）。"""
    vector = _get_model().encode(text, normalize_embeddings=True, show_progress_bar=False)
    return vector.tolist()
