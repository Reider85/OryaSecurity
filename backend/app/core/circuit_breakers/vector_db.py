"""Circuit breaker for the Qdrant vector DB (EP-14 / Prompt 36, ADR-0008).

Fallback mode is ``degrade``: when the breaker short-circuits a search the
slow path skips the vector component and the verdict rests on the rules
(fast path) only.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import numpy as np
import structlog
from pybreaker import CircuitBreakerError

from app.core.circuit_breaker import (
    AsyncCircuitBreaker,
    HalfOpenProbeSkipped,
    LogCircuitBreakerListener,
    MetricsCircuitBreakerListener,
    record_fallback,
)
from app.core.detectors.vector_search import SearchResult, VectorSearcher

logger = structlog.get_logger()

COMPONENT = "vector_db"


class VectorDBCircuitBreaker:
    """Circuit breaker around :class:`VectorSearcher` Qdrant calls.

    Args:
        searcher: Underlying Qdrant searcher (its ``search_or_raise`` is
            guarded by the breaker).
        fail_max: Failures inside ``failure_window`` that trip the breaker.
        reset_timeout: Seconds in OPEN before half-open probing starts.
        failure_window: Sliding window for the failure count (spec: 10s).
        probe_ratio: Share of traffic admitted as probes in half-open (10%).
        probe_rng: Injectable uniform [0, 1) sampler for deterministic tests.
    """

    component = COMPONENT

    def __init__(
        self,
        searcher: VectorSearcher,
        *,
        fail_max: int = 5,
        reset_timeout: float = 30.0,
        failure_window: float = 10.0,
        probe_ratio: float = 0.1,
        probe_rng: Optional[Callable[[], float]] = None,
    ) -> None:
        self._searcher = searcher
        self._breaker = AsyncCircuitBreaker(
            name=COMPONENT,
            fail_max=fail_max,
            reset_timeout=reset_timeout,
            failure_window=failure_window,
            probe_ratio=probe_ratio,
            probe_rng=probe_rng,
            listeners=[
                LogCircuitBreakerListener(COMPONENT),
                MetricsCircuitBreakerListener(COMPONENT),
            ],
        )

    @property
    def state(self) -> str:
        """Breaker state as ``closed`` | ``open`` | ``half_open``."""
        return self._breaker.current_state.replace("-", "_")

    async def search(
        self,
        embedding: np.ndarray,
        top_k: Optional[int] = None,
    ) -> Optional[list[SearchResult]]:
        """Run a Qdrant search behind the breaker.

        Returns ``None`` when the call was degraded to rule-only mode
        (breaker open, half-open non-probe, or upstream failure) — the
        caller treats ``None`` as "vector component unavailable".
        """
        try:
            return await self._breaker.call_async(
                self._searcher.search_or_raise, embedding, top_k=top_k
            )
        except HalfOpenProbeSkipped:
            record_fallback(COMPONENT, reason="half_open_probe_skipped")
            return None
        except CircuitBreakerError:
            record_fallback(COMPONENT, reason="open")
            return None
        except Exception as exc:  # noqa: BLE001 — degrade, never break the caller
            record_fallback(COMPONENT, reason="upstream_error", error=str(exc))
            return None


# Global singleton (wired in main.init_post_factum_stack)
_vector_db_breaker: Optional[VectorDBCircuitBreaker] = None


def get_vector_db_breaker() -> Optional[VectorDBCircuitBreaker]:
    return _vector_db_breaker


def set_vector_db_breaker(breaker: Optional[VectorDBCircuitBreaker]) -> None:
    global _vector_db_breaker
    _vector_db_breaker = breaker
