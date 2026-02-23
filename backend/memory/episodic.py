"""
memory/episodic.py — Qdrant embedded vector store for episodic memory.

Stores conversation summaries as vector embeddings so the assistant can
recall semantically similar past interactions.

Collection: "conversations"
Schema per point:
    id: UUID string (used as Qdrant point ID hash)
    vector: 384-dim float (all-MiniLM-L6-v2)
    payload: {
        conversation_id: str,
        summary: str,
        timestamp: str (ISO 8601),
        metadata: dict,
    }
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config.loader import cfg
from backend.config.logging import get_logger
from backend.memory.embeddings import embed, embed_many, embedding_dim

logger = get_logger(__name__)

COLLECTION_NAME = "conversations"
_client = None


# ---------------------------------------------------------------------------
# Client initialisation
# ---------------------------------------------------------------------------

def _qdrant_path() -> Path:
    p = Path(cfg.database.path).parent / "qdrant"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _get_client():
    global _client
    if _client is None:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        path = str(_qdrant_path())
        logger.info("Initialising Qdrant embedded client", extra={"path": path})
        _client = QdrantClient(path=path)

        # Create collection if it doesn't exist
        existing = {c.name for c in _client.get_collections().collections}
        if COLLECTION_NAME not in existing:
            _client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=embedding_dim(), distance=Distance.COSINE),
            )
            logger.info("Created Qdrant collection", extra={"collection": COLLECTION_NAME})
    return _client


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _point_id(conversation_id: str, timestamp: str) -> int:
    """Deterministic integer point ID from conversation_id + timestamp."""
    raw = f"{conversation_id}:{timestamp}"
    return int(hashlib.md5(raw.encode()).hexdigest(), 16) % (2**63)


async def store_summary(
    conversation_id: str,
    summary: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Embed and store a conversation summary in Qdrant.
    Returns the point ID string.
    """
    from qdrant_client.models import PointStruct

    ts = datetime.now(timezone.utc).isoformat()
    vector = await embed(summary)
    point_id = _point_id(conversation_id, ts)

    client = _get_client()
    client.upsert(
        collection_name=COLLECTION_NAME,
        points=[
            PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "conversation_id": conversation_id,
                    "summary": summary,
                    "timestamp": ts,
                    "metadata": metadata or {},
                },
            )
        ],
    )
    logger.info("Stored episodic memory", extra={"conversation_id": conversation_id})
    return str(point_id)


async def search(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Return up to `top_k` most semantically similar summaries.
    Each result: { conversation_id, summary, timestamp, metadata, score }
    """
    vector = await embed(query)
    client = _get_client()

    results = client.search(
        collection_name=COLLECTION_NAME,
        query_vector=vector,
        limit=top_k,
        with_payload=True,
    )

    return [
        {
            "conversation_id": r.payload.get("conversation_id", ""),
            "summary": r.payload.get("summary", ""),
            "timestamp": r.payload.get("timestamp", ""),
            "metadata": r.payload.get("metadata", {}),
            "score": r.score,
        }
        for r in results
    ]


async def list_summaries(limit: int = 50) -> List[Dict[str, Any]]:
    """List recent summaries (for the memory browser UI)."""
    client = _get_client()
    results, _ = client.scroll(
        collection_name=COLLECTION_NAME,
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )
    items = [
        {
            "id": str(r.id),
            "conversation_id": r.payload.get("conversation_id", ""),
            "summary": r.payload.get("summary", ""),
            "timestamp": r.payload.get("timestamp", ""),
        }
        for r in results
    ]
    items.sort(key=lambda x: x["timestamp"], reverse=True)
    return items


async def delete_summary(point_id: str) -> None:
    """Delete a specific episodic memory point."""
    client = _get_client()
    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=[int(point_id)],
    )
    logger.info("Deleted episodic memory", extra={"point_id": point_id})


async def deduplicate(similarity_threshold: float = 0.98) -> int:
    """
    Remove near-duplicate summaries (cosine sim > threshold).
    Returns number of points deleted.
    """
    client = _get_client()
    points, _ = client.scroll(
        collection_name=COLLECTION_NAME,
        limit=1000,
        with_payload=True,
        with_vectors=True,
    )

    deleted = 0
    seen_ids: set[int] = set()
    to_delete: list[int] = []

    for point in points:
        if point.id in seen_ids:
            continue
        seen_ids.add(point.id)

        # Find similar points
        similar = client.search(
            collection_name=COLLECTION_NAME,
            query_vector=point.vector,
            limit=10,
            score_threshold=similarity_threshold,
            with_payload=False,
        )

        for s in similar:
            if s.id != point.id and s.id not in seen_ids:
                to_delete.append(s.id)
                seen_ids.add(s.id)

    if to_delete:
        client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=to_delete,
        )
        deleted = len(to_delete)
        logger.info("Deduplicated episodic memories", extra={"deleted": deleted})

    return deleted
