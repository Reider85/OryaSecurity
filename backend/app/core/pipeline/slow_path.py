from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional, AsyncContextManager

import numpy as np
import structlog

from app.core.cache_embedding import embedding_cache
from app.core.detectors.vector_search import SearchResult
from app.core.detectors.ml_classifier import ClassificationResult, PromptClassifier
from app.core.metrics import (
    scanner_slow_path_duration_seconds,
    scanner_slow_path_total,
    scanner_slow_path_degraded_total,
)

logger = structlog.get_logger()

# Sentinel for circuit breaker stop
_STOP = object()


@dataclass
class SlowPathResult:
    """Result from slow path pipeline analysis."""

    embedding: Optional[np.ndarray] = None
    similar_attacks: list[SearchResult] = field(default_factory=list)
    classification: Optional[ClassificationResult] = None
    aggregated: dict[str, Any] = field(default_factory=dict)
    degraded_components: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for serialization."""
        return {
            "embedding_present": self.embedding is not None,
            "similar_attacks_count": len(self.similar_attacks),
            "classification": self.classification.to_dict() if self.classification else None,
            "aggregated": self.aggregated,
            "degraded_components": self.degraded_components,
            "latency_ms": self.latency_ms,
            "breakdown": self.breakdown,
        }


class CircuitBreaker:
    """Minimal async circuit breaker for slow path components.

    Implements closed→open→half-open states with fail-open degradation.
    Designed to be replaced by pybreaker in EP-14.

    Args:
        name: Component name for metrics/logging
        fail_max: Max failures before opening
        reset_timeout: Seconds to wait before trying again (half-open)
    """

    def __init__(self, name: str, fail_max: int = 5, reset_timeout: float = 30.0):
        self.name = name
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self._failures = 0
        self._last_failure_time: float = 0.0
        self._half_open_fails = 0
        self._state = "closed"  # closed | open | half_open

    @property
    def state(self) -> str:
        return self._state

    def _record_failure(self) -> None:
        """Record a failure and potentially open the breaker."""
        now = time.monotonic()
        self._failures += 1
        self._last_failure_time = now
        self._half_open_fails += 1

        if self._failures >= self.fail_max and self._state == "closed":
            self._state = "open"
            logger.warning(
                "circuit_breaker_opened",
                component=self.name,
                failures=self._failures,
                reset_timeout=self.reset_timeout,
            )

    def _record_success(self) -> None:
        """Record success and reset failure counters."""
        self._failures = 0
        self._half_open_fails = 0
        if self._state == "open":
            self._state = "closed"
            logger.info("circuit_breaker_reset", component=self.name)

    def should_call(self) -> bool:
        """Check if the call should be attempted (not in open state)."""
        if self._state == "open":
            # Check if reset timeout has passed
            if time.monotonic() - self._last_failure_time >= self.reset_timeout:
                self._state = "half_open"
                self._half_open_fails = 0
                logger.info("circuit_breaker_half_open", component=self.name)
                return True
            return False
        return True

    async def call(self, func: Any, *args: Any, **kwargs: Any) -> Optional[Any]:
        """Execute function with circuit breaker protection.

        Returns:
            Function result or None if degraded (breaker open or call failed)
        """
        if not self.should_call():
            # Open state, skip call
            scanner_slow_path_degraded_total.labels(component=self.name).inc()
            return None

        try:
            result = await func(*args, **kwargs)
            if result is not None:
                self._record_success()
            return result
        except Exception as exc:
            self._record_failure()
            scanner_slow_path_degraded_total.labels(component=self.name).inc()
            logger.warning(
                "circuit_breaker_failure",
                component=self.name,
                error=str(exc),
                state=self._state,
            )
            return None


class SlowPath:
    """Two-tier pipeline slow path: embedding + vector search + ML classifier.

    Performs deep analysis of suspicious prompts flagged by fast path.
    Steps:
    1. Embed prompt (cache → batcher → client)
    2. Vector search (if embedding available)
    3. ML classifier
    4. Aggregate results for PDP decision

    Args:
        embedding_batcher: Optional EmbeddingBatcher for micro-batching
        embedding_cache: Optional cache instance (defaults to global)
        vector_searcher: Optional VectorSearcher (defaults to global)
        classifier: Optional PromptClassifier (defaults to global)
    """

    def __init__(
        self,
        embedding_batcher: Optional[Any] = None,
        embedding_cache: Optional[Any] = None,
        vector_searcher: Optional[Any] = None,
        classifier: Optional[PromptClassifier] = None,
    ):
        self.embedding_batcher = embedding_batcher
        self.embedding_cache = embedding_cache or embedding_cache
        self.vector_searcher = vector_searcher
        self.classifier = classifier

        # Circuit breakers for each component
        self.embedding_cb = CircuitBreaker("embedding", fail_max=5, reset_timeout=30.0)
        self.vector_search_cb = CircuitBreaker("vector_search", fail_max=5, reset_timeout=30.0)
        self.classifier_cb = CircuitBreaker("ml_classifier", fail_max=5, reset_timeout=30.0)

    def _get_embedding_batcher(self) -> Any:
        """Get shared embedding batcher or None."""
        if self.embedding_batcher is not None:
            return self.embedding_batcher
        from app.ml.batcher import get_shared_embedding_batcher
        return get_shared_embedding_batcher()

    def _get_vector_searcher(self) -> Any:
        """Get shared vector searcher or None."""
        if self.vector_searcher is not None:
            return self.vector_searcher
        from app.core.detectors.vector_search import get_shared_vector_searcher
        return get_shared_vector_searcher()

    def _get_classifier(self) -> Any:
        """Get shared classifier or None."""
        if self.classifier is not None:
            return self.classifier
        from app.core.detectors.ml_classifier import get_shared_classifier
        return get_shared_classifier()

    def _aggregate_results(
        self,
        embedding: Optional[np.ndarray],
        similar_attacks: list[SearchResult],
        classification: Optional[ClassificationResult],
    ) -> dict[str, Any]:
        """Aggregate vector search and ML classifier results.

        Returns:
            Dictionary with combined threat evidence
        """
        aggregated = {
            "vector_max_score": 0.0,
            "vector_hits_above_threshold": 0,
            "vector_threat_types": [],
            "classifier_top_class": None,
            "classifier_confidence": 0.0,
            "classifier_is_suspicious": False,
            "threat_score": 0.0,
            "recommended_evidence": [],
        }

        # Vector search aggregation
        if similar_attacks:
            from app.config import settings
            max_score = max(hit.score for hit in similar_attacks)
            hits_above_threshold = [
                hit for hit in similar_attacks
                if hit.score >= settings.vector_match_threshold
            ]
            
            aggregated.update({
                "vector_max_score": max_score,
                "vector_hits_above_threshold": len(hits_above_threshold),
                "vector_threat_types": list({
                    hit.payload.get("type", "unknown") for hit in hits_above_threshold
                }),
            })
            
            # If any high-confidence vector match, boost threat score
            if max_score >= settings.vector_match_threshold:
                aggregated["threat_score"] += max_score * 0.6

        # ML classifier aggregation
        if classification:
            aggregated.update({
                "classifier_top_class": classification.top_class,
                "classifier_confidence": classification.confidence,
                "classifier_is_suspicious": classification.is_suspicious,
            })
            
            # Boost threat score for suspicious classifications
            if classification.is_suspicious:
                aggregated["threat_score"] += classification.confidence * 0.4

            # Add to recommended evidence if high confidence
            if classification.confidence >= 0.85:
                aggregated["recommended_evidence"].append({
                    "type": "ml_classifier",
                    "top_class": classification.top_class,
                    "confidence": classification.confidence,
                })

        # Add vector hits to evidence if above threshold
        if hits_above_threshold:
            aggregated["recommended_evidence"].extend([
                {
                    "type": "vector_search",
                    "score": hit.score,
                    "attack_type": hit.payload.get("type"),
                    "vector_id": str(hit.id),
                }
                for hit in hits_above_threshold
            ])

        return aggregated

    async def process(self, text: str) -> SlowPathResult:
        """Process text through slow path pipeline.

        Args:
            text: Input text to analyze

        Returns:
            SlowPathResult with analysis results and latency breakdown
        """
        start_time = time.perf_counter()
        degraded_components = []
        breakdown = {}

        # Step 1: Embedding
        embed_start = time.perf_counter()
        embedding = await self.embedding_cb.call(
            self._get_embedding,
            text
        )
        embed_latency = time.perf_counter() - embed_start
        breakdown["embed_ms"] = embed_latency * 1000

        if embedding is None:
            degraded_components.append("embedding")

        # Step 2: Vector search (if embedding available)
        similar_attacks = []
        if embedding is not None:
            vector_start = time.perf_counter()
            similar_attacks = await self.vector_search_cb.call(
                self._get_vector_searcher().search,
                embedding,
                top_k=10  # Use qdrant_top_k from settings
            )
            vector_latency = time.perf_counter() - vector_start
            breakdown["vector_ms"] = vector_latency * 1000

            if similar_attacks is None:
                similar_attacks = []
                degraded_components.append("vector_search")

        # Step 3: ML classifier (can run in parallel with vector search)
        classification = None
        if text:  # Always try classification, even if degraded
            classifier_start = time.perf_counter()
            classification = await self.classifier_cb.call(
                self._get_classifier().classify,
                text
            )
            classifier_latency = time.perf_counter() - classifier_start
            breakdown["classifier_ms"] = classifier_latency * 1000

            if classification is None:
                degraded_components.append("ml_classifier")

        # Step 4: Aggregate results
        aggregate_start = time.perf_counter()
        aggregated = self._aggregate_results(embedding, similar_attacks, classification)
        aggregate_latency = time.perf_counter() - aggregate_start
        breakdown["aggregate_ms"] = aggregate_latency * 1000

        # Determine outcome for metrics
        outcome = "degraded" if degraded_components else (
            "threat" if aggregated["threat_score"] > 0.0 else "clean"
        )

        total_latency = time.perf_counter() - start_time
        latency_ms = total_latency * 1000
        breakdown["total_ms"] = latency_ms

        # Record metrics
        scanner_slow_path_duration_seconds.observe(total_latency)
        scanner_slow_path_total.labels(outcome=outcome).inc()

        logger.info(
            "slow_path_complete",
            outcome=outcome,
            degraded_components=degraded_components,
            threat_score=aggregated["threat_score"],
            latency_ms=round(latency_ms, 2),
        )

        return SlowPathResult(
            embedding=embedding,
            similar_attacks=similar_attacks,
            classification=classification,
            aggregated=aggregated,
            degraded_components=degraded_components,
            latency_ms=latency_ms,
            breakdown=breakdown,
        )

    async def _get_embedding(self, text: str) -> Optional[np.ndarray]:
        """Get embedding with cache → batcher → client fallback."""
        # Try cache first
        cached = await self.embedding_cache.get(text)
        if cached is not None:
            return cached

        # Try batcher
        batcher = self._get_embedding_batcher()
        if batcher is not None:
            embedding = await batcher.embed(text)
            if embedding is not None:
                await self.embedding_cache.set(text, embedding)
                return embedding

        # Fallback to direct embedding client
        from app.core.detectors.embedding import get_shared_embedding_client
        client = get_shared_embedding_client()
        if client is not None and client.available:
            embedding = await client.embed(text)
            if embedding is not None:
                await self.embedding_cache.set(text, embedding)
            return embedding

        return None


# Global singleton
_slow_path: Optional[SlowPath] = None


def get_slow_path() -> SlowPath:
    """Get global slow path instance (lazy initialization)."""
    global _slow_path
    if _slow_path is None:
        _slow_path = SlowPath()
        logger.info("slow_path_initialized")
    return _slow_path


def set_slow_path(slow_path: Optional[SlowPath]) -> None:
    """Set global slow path instance."""
    global _slow_path
    _slow_path = slow_path
    logger.info("slow_path_updated")