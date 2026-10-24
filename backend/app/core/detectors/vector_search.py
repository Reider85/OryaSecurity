from __future__ import annotations

import structlog
from dataclasses import dataclass, field
from typing import Any, Optional
import time

import numpy as np

logger = structlog.get_logger()


@dataclass
class SearchResult:
    """Single vector-search hit from the prompt-attacks collection."""

    id: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)


class VectorSearcher:
    """Async vector search against a Qdrant collection.

    Degrades gracefully: any connection/infra error is logged, a degraded
    metric is incremented and an empty result list is returned so the
    post-factum pipeline keeps running with the remaining detectors.
    """

    def __init__(
        self,
        url: str,
        collection: str,
        top_k: int = 10,
        timeout: float = 2.0,
        ef_search: Optional[int] = None,
        qdrant_client: Any = None,
    ):
        self.url = url
        self.collection = collection
        self.top_k = top_k
        self.timeout = timeout
        self.ef_search = ef_search
        self._client = qdrant_client
        self._client_owned = qdrant_client is None

    def _get_client(self) -> Any:
        if self._client is None:
            from qdrant_client import AsyncQdrantClient

            self._client = AsyncQdrantClient(url=self.url, timeout=self.timeout)
        return self._client

    @property
    def available(self) -> bool:
        return True  # availability is detected per-call via degrade path

    async def search(
        self,
        embedding: np.ndarray,
        top_k: Optional[int] = None,
    ) -> list[SearchResult]:
        """Search for similar attack vectors. Returns [] on any failure."""
        from app.core.metrics import scanner_post_factum_degraded_total, scanner_vector_search_duration_seconds

        limit = top_k or self.top_k
        start_time = time.monotonic()
        try:
            client = self._get_client()
            response = await client.query_points(
                collection_name=self.collection,
                query=embedding.tolist(),
                limit=limit,
                with_payload=True,
            )
            results: list[SearchResult] = []
            for point in response.points:
                results.append(
                    SearchResult(
                        id=str(point.id),
                        score=float(point.score),
                        payload=dict(point.payload or {}),
                    )
                )
            scanner_vector_search_duration_seconds.observe(time.monotonic() - start_time)
            return results
        except Exception as exc:  # noqa: BLE001 — degrade, never break the caller
            scanner_vector_search_duration_seconds.observe(time.monotonic() - start_time)
            scanner_post_factum_degraded_total.labels(component="vector_search").inc()
            logger.warning(
                "vector_search_degraded",
                collection=self.collection,
                error=str(exc),
            )
            return []

    async def close(self) -> None:
        if self._client is not None and self._client_owned:
            close_fn = getattr(self._client, "close", None)
            if close_fn is not None:
                try:
                    await close_fn()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("qdrant_client_close_failed", error=str(exc))
            self._client = None


# Global singleton
_shared_vector_searcher: Optional[VectorSearcher] = None


def get_shared_vector_searcher() -> Optional[VectorSearcher]:
    return _shared_vector_searcher


def set_shared_vector_searcher(searcher: Optional[VectorSearcher]) -> None:
    global _shared_vector_searcher
    _shared_vector_searcher = searcher
