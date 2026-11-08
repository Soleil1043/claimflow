"""BGE-M3 Embedding 服务（D003：本地 sentence-transformers）。

惰性单例加载模型（首次加载约 2GB，避免 import 时阻塞）；
1024 维向量，同时支持批量编码（入库）与单条编码（查询）。

异步调用方一律用 aembed_query / aembed_texts（T219/I18）：编码是同步 CPU 推理
（100-500ms/条），在事件循环里直接调用会让同进程所有请求、租约心跳、其他案件
交付全部停摆，守卫 asyncio.timeout 也只能在 await 点生效、编码期间无法取消。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# BGE-M3 输出维度（Qdrant collection 向量维度需与此一致）
EMBEDDING_DIM = 1024

# 模型单例 + 加载锁：编码移入线程池后，冷启动时多个并发查询可能同时触发加载——
# 无锁会重复加载约 2GB 模型（functools.cache 不防并发首调）
_model: Any = None
_model_lock = threading.Lock()


def _get_model() -> Any:
    """惰性加载 sentence-transformers 模型（进程内单例，线程安全）。"""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer

                # 本地目录优先（容器/离线：直载快照目录，绕开 hub 缓存校验与在线版本检查）
                source = settings.embedding_model_path or settings.embedding_model
                _model = SentenceTransformer(source, device=settings.embedding_device)
                log.info(
                    "embedding_model_loaded", model=source, device=settings.embedding_device
                )
    return _model


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


async def aembed_texts(texts: list[str]) -> list[list[float]]:
    """批量编码的异步版：在线程池执行，事件循环不停摆（T219/I18）。"""
    return await asyncio.to_thread(embed_texts, texts)


async def aembed_query(text: str) -> list[float]:
    """单条编码的异步版：在线程池执行，守卫超时可在编码期间生效（T219/I18）。

    超时取消只停止等待——线程内的编码仍会跑完（CPU 推理不可中断），但事件循环
    与其他请求不再被连坐。
    """
    return await asyncio.to_thread(embed_query, text)


def preload_embedding_model() -> None:
    """预热：进程启动时同步加载模型（约 10-20s CPU，一次性成本）。

    必须在事件循环外执行（FastAPI lifespan 用 asyncio.to_thread 包装）——
    否则首次 RAG/claim_rule_rag 调用的同步加载会阻塞事件循环，把同 Worker
    内其他工具的守卫超时窗口连坐引爆（T048 评测实测：BGE-M3 冷加载 19s
    → diagnosis_matcher 判超时）。
    """
    _get_model()
    log.info("embedding_model_preloaded")
