"""Unit tests for the circuit breaker infrastructure and the Vector DB
breaker (EP-14 / Prompt 36, ADR-0008): closed/open/half-open transitions,
5-errors-in-10s window, 10% half-open probe sampling, log + Prometheus
listeners and the degrade (rule-only) fallback."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import numpy as np
import pybreaker
import pytest
from prometheus_client import REGISTRY
from structlog.testing import capture_logs

from app.core.circuit_breaker import (
    AsyncCircuitBreaker,
    HalfOpenProbeSkipped,
    LogCircuitBreakerListener,
    MetricsCircuitBreakerListener,
    record_fallback,
)
from app.core.circuit_breakers.vector_db import (
    VectorDBCircuitBreaker,
    get_vector_db_breaker,
    set_vector_db_breaker,
)
from app.core.detectors.vector_search import SearchResult

STATE_METRIC = "scanner_circuit_breaker_state"
FAILURES_METRIC = "scanner_circuit_breaker_failures_total"
FALLBACK_METRIC = "scanner_circuit_breaker_fallback_invocations_total"


async def _ok(*args, **kwargs):
    return "ok"


async def _boom(*args, **kwargs):
    raise RuntimeError("upstream down")


def _breaker(**kwargs) -> AsyncCircuitBreaker:
    kwargs.setdefault("name", "test")
    kwargs.setdefault("fail_max", 5)
    kwargs.setdefault("reset_timeout", 30.0)
    kwargs.setdefault("probe_rng", lambda: 0.0)  # always win the probe draw
    return AsyncCircuitBreaker(**kwargs)


async def _trip(breaker: AsyncCircuitBreaker, failures: int = 5) -> None:
    for _ in range(failures):
        with pytest.raises(RuntimeError):
            await breaker.call_async(_boom)


def _elapsed_reset_timeout(breaker: AsyncCircuitBreaker) -> None:
    """Backdate opened_at so the OPEN cooldown has elapsed."""
    breaker._state_storage.opened_at = datetime.now(UTC) - timedelta(
        seconds=breaker.reset_timeout + 1
    )


class TestAsyncCircuitBreaker:
    async def test_closed_state_passes_calls(self):
        breaker = _breaker()
        assert await breaker.call_async(_ok) == "ok"
        assert breaker.current_state == pybreaker.STATE_CLOSED

    async def test_opens_after_fail_max_failures_in_window(self):
        breaker = _breaker(fail_max=5)
        calls = 0

        async def failing(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise RuntimeError("boom")

        for _ in range(5):
            with pytest.raises(RuntimeError):
                await breaker.call_async(failing)

        assert breaker.current_state == pybreaker.STATE_OPEN
        with pytest.raises(pybreaker.CircuitBreakerError):
            await breaker.call_async(failing)
        assert calls == 5  # short-circuited: upstream untouched after opening

    async def test_failures_outside_window_do_not_trip(self):
        breaker = _breaker(fail_max=3, failure_window=0.05)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await breaker.call_async(_boom)
        await asyncio.sleep(0.06)  # both failures age out of the window
        with pytest.raises(RuntimeError):
            await breaker.call_async(_boom)
        assert breaker.current_state == pybreaker.STATE_CLOSED

    async def test_open_short_circuits_before_reset_timeout(self):
        breaker = _breaker()
        await _trip(breaker)
        with pytest.raises(pybreaker.CircuitBreakerError):
            await breaker.call_async(_ok)
        assert breaker.current_state == pybreaker.STATE_OPEN

    async def test_half_open_probe_success_closes(self):
        breaker = _breaker()
        await _trip(breaker)
        _elapsed_reset_timeout(breaker)
        assert await breaker.call_async(_ok) == "ok"
        assert breaker.current_state == pybreaker.STATE_CLOSED

    async def test_half_open_admits_only_probe_ratio(self):
        breaker = _breaker(probe_ratio=0.1, probe_rng=lambda: 0.95)  # loses the draw
        await _trip(breaker)
        _elapsed_reset_timeout(breaker)
        with pytest.raises(HalfOpenProbeSkipped):
            await breaker.call_async(_ok)
        assert breaker.current_state == pybreaker.STATE_HALF_OPEN

    async def test_half_open_probe_failure_reopens(self):
        breaker = _breaker()
        await _trip(breaker)
        _elapsed_reset_timeout(breaker)
        with pytest.raises(RuntimeError):
            await breaker.call_async(_boom)
        assert breaker.current_state == pybreaker.STATE_OPEN

    async def test_excluded_exception_counts_as_success(self):
        breaker = _breaker(fail_max=1, exclude=[RuntimeError])
        with pytest.raises(RuntimeError):
            await breaker.call_async(_boom)
        # Business exceptions propagate but never trip the breaker.
        assert breaker.current_state == pybreaker.STATE_CLOSED
        assert breaker.fail_counter == 0


class TestListeners:
    async def test_log_listener_reports_state_changes(self):
        breaker = _breaker(
            fail_max=2, listeners=[LogCircuitBreakerListener("vector_db_test")]
        )
        with capture_logs() as logs:
            await _trip(breaker, failures=2)
            _elapsed_reset_timeout(breaker)
            assert await breaker.call_async(_ok) == "ok"

        events = {entry["event"] for entry in logs}
        assert {
            "circuit_breaker_failure",
            "circuit_breaker_opened",
            "circuit_breaker_half_open",
            "circuit_breaker_reset",
        } <= events

    async def test_metrics_listener_exports_state_and_failures(self):
        component = "cb_metrics_test"
        breaker = _breaker(
            fail_max=1, listeners=[MetricsCircuitBreakerListener(component)]
        )
        assert (
            REGISTRY.get_sample_value(STATE_METRIC, {"component": component}) == 0.0
        )
        with pytest.raises(RuntimeError):
            await breaker.call_async(_boom)
        assert (
            REGISTRY.get_sample_value(STATE_METRIC, {"component": component}) == 1.0
        )
        assert (
            REGISTRY.get_sample_value(FAILURES_METRIC, {"component": component}) == 1.0
        )

    def test_record_fallback_counts_invocations(self):
        component = "cb_fallback_test"
        before = REGISTRY.get_sample_value(
            FALLBACK_METRIC, {"component": component, "mode": "degrade"}
        )
        record_fallback(component, mode="degrade")
        assert (
            REGISTRY.get_sample_value(
                FALLBACK_METRIC, {"component": component, "mode": "degrade"}
            )
            == (before or 0.0) + 1.0
        )


def _hits() -> list[SearchResult]:
    return [SearchResult(id="1", score=0.95, payload={"type": "jailbreak"})]


def _vector_breaker(searcher: AsyncMock, **kwargs) -> VectorDBCircuitBreaker:
    kwargs.setdefault("probe_rng", lambda: 0.0)
    return VectorDBCircuitBreaker(searcher, **kwargs)


class TestVectorDBCircuitBreaker:
    async def test_search_passes_through_when_closed(self):
        searcher = AsyncMock()
        hits = _hits()
        searcher.search_or_raise.return_value = hits
        cb = _vector_breaker(searcher)

        result = await cb.search(np.zeros(4, dtype=np.float32), top_k=10)

        assert result == hits
        assert cb.state == "closed"
        searcher.search_or_raise.assert_awaited_once()

    async def test_search_degrades_when_open(self):
        searcher = AsyncMock()
        searcher.search_or_raise.side_effect = RuntimeError("qdrant down")
        cb = _vector_breaker(searcher, fail_max=5)
        emb = np.zeros(4, dtype=np.float32)
        before = REGISTRY.get_sample_value(
            FALLBACK_METRIC, {"component": "vector_db", "mode": "degrade"}
        )

        for _ in range(5):
            assert await cb.search(emb) is None
        assert cb.state == "open"

        # short-circuit: the upstream is no longer touched
        assert await cb.search(emb) is None
        assert searcher.search_or_raise.await_count == 5

        assert REGISTRY.get_sample_value(STATE_METRIC, {"component": "vector_db"}) == 1.0
        assert (
            REGISTRY.get_sample_value(
                FALLBACK_METRIC, {"component": "vector_db", "mode": "degrade"}
            )
            == (before or 0.0) + 6.0
        )

    async def test_search_upstream_error_degrades_without_opening(self):
        searcher = AsyncMock()
        searcher.search_or_raise.side_effect = RuntimeError("timeout")
        cb = _vector_breaker(searcher, fail_max=5)

        assert await cb.search(np.zeros(4, dtype=np.float32)) is None
        assert cb.state == "closed"  # 1 failure < fail_max

    async def test_half_open_probe_recovers(self):
        searcher = AsyncMock()
        searcher.search_or_raise.side_effect = RuntimeError("qdrant down")
        cb = _vector_breaker(searcher, fail_max=2)
        emb = np.zeros(4, dtype=np.float32)
        for _ in range(2):
            assert await cb.search(emb) is None
        assert cb.state == "open"

        hits = _hits()
        searcher.search_or_raise.side_effect = None
        searcher.search_or_raise.return_value = hits
        _elapsed_reset_timeout(cb._breaker)

        assert await cb.search(emb) == hits
        assert cb.state == "closed"

    async def test_half_open_non_probe_returns_none(self):
        searcher = AsyncMock()
        searcher.search_or_raise.side_effect = RuntimeError("qdrant down")
        cb = _vector_breaker(searcher, fail_max=2, probe_rng=lambda: 0.95)
        emb = np.zeros(4, dtype=np.float32)
        for _ in range(2):
            assert await cb.search(emb) is None
        _elapsed_reset_timeout(cb._breaker)

        assert await cb.search(emb) is None  # lost the 10% draw
        assert cb.state == "half_open"
        assert searcher.search_or_raise.await_count == 2

    def test_singleton_roundtrip(self):
        cb = _vector_breaker(AsyncMock())
        set_vector_db_breaker(cb)
        try:
            assert get_vector_db_breaker() is cb
        finally:
            set_vector_db_breaker(None)
        assert get_vector_db_breaker() is None


class TestSlowPathDegrade:
    async def test_slow_path_marks_vector_degraded_when_breaker_opens(self):
        from app.core.detectors.ml_classifier import ClassificationResult
        from app.core.pipeline.slow_path import SlowPath

        batcher = AsyncMock()
        batcher.embed.return_value = np.zeros(4, dtype=np.float32)
        searcher = AsyncMock()
        searcher.search_or_raise.side_effect = RuntimeError("qdrant down")
        classifier = AsyncMock()
        classifier.classify.return_value = ClassificationResult(
            top_class="benign",
            confidence=0.9,
            all_classes={"benign": 0.9},
        )

        slow_path = SlowPath(
            embedding_batcher=batcher,
            vector_searcher=searcher,
            classifier=classifier,
        )
        slow_path._get_embedding = AsyncMock(return_value=np.zeros(4, dtype=np.float32))

        for _ in range(5):
            result = await slow_path.process("hello")
            assert "vector_search" in result.degraded_components

        assert slow_path.vector_db_cb.state == "open"

        # Breaker open: degrade to rules-only, upstream stays untouched.
        result = await slow_path.process("hello")
        assert "vector_search" in result.degraded_components
        assert result.similar_attacks == []
        assert searcher.search_or_raise.await_count == 5
