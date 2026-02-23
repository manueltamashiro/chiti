"""
memory/embeddings.py — Async wrapper around sentence-transformers.

Uses all-MiniLM-L6-v2 (~80MB) for local, fast, 384-dim embeddings.
Runs CPU inference in a thread pool to avoid blocking the event loop.
Caches embeddings for identical text to avoid re-computing.
"""

from __future__ import annotations

import asyncio
import hashlib
from functools import lru_cache
from typing import List

from backend.config.logging import get_logger

logger = get_logger(__name__)

_MODEL_NAME = "all-MiniLM-L6-v2"
_model = None
_cache: dict[str, List[float]] = {}


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model", extra={"model": _MODEL_NAME})
        _model = SentenceTransformer(_MODEL_NAME)
        logger.info("Embedding model loaded")
    return _model


def _embed_sync(text: str) -> List[float]:
    """Synchronous embedding — runs in thread pool."""
    key = hashlib.sha256(text.encode()).hexdigest()
    if key in _cache:
        return _cache[key]
    model = _get_model()
    vec: List[float] = model.encode(text, normalize_embeddings=True).tolist()
    _cache[key] = vec
    return vec


async def embed(text: str) -> List[float]:
    """Async embed a single string. Uses thread pool so event loop is never blocked."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _embed_sync, text)


async def embed_many(texts: List[str]) -> List[List[float]]:
    """Embed a list of strings concurrently."""
    return await asyncio.gather(*[embed(t) for t in texts])


def embedding_dim() -> int:
    """Return the embedding dimension (384 for MiniLM-L6-v2)."""
    return 384
