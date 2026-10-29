"""Circuit breaker infrastructure for upstream components (EP-14 / ADR-0008).

Thin asyncio layer on top of :mod:`pybreaker`:

* :class:`AsyncCircuitBreaker` — drives pybreaker's state machine from async
  code (pybreaker's own ``call_async`` requires tornado), opens after
  ``fail_max`` failures inside a sliding ``failure_window`` and lets only
  ``probe_ratio`` (10%) of traffic through in the half-open state.
* :class:`LogCircuitBreakerListener` — structlog events on state changes.
* :class:`MetricsCircuitBreakerListener` — Prometheus gauge/counter updates.
* :func:`record_fallback` — counts fallback (degrade/fail-open/fail-closed)
  invocations when a breaker short-circuits a call.
"""

from __future__ import annotations

import random
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

import pybreaker
import structlog

from app.core.metrics import (
    scanner_circuit_breaker_failures_total,
    scanner_circuit_breaker_fallback_invocations_total,
    scanner_circuit_breaker_state,
)

logger = structlog.get_logger()

# Prometheus representation of the breaker state (ADR-0008 / Prompt 39).
STATE_VALUES: dict[str, float] = {
    pybreaker.STATE_CLOSED: 0.0,
    pybreaker.STATE_OPEN: 1.0,
    pybreaker.STATE_HALF_OPEN: 2.0,
}


class HalfOpenProbeSkipped(pybreaker.CircuitBreakerError):
    """Raised when a call hits a half-open breaker but loses the 10% probe draw."""


class AsyncCircuitBreaker(pybreaker.CircuitBreaker):
    """pybreaker circuit breaker usable from asyncio coroutines.

    Differences from stock pybreaker:

    * :meth:`call_async` is a real ``async def`` (the upstream implementation
      needs tornado, which this project does not use).
    * The breaker trips when ``fail_max`` failures occurred inside the sliding
      ``failure_window`` (spec: 5 errors in 10s) instead of counting only
      consecutive failures.
    * In the half-open state only ``probe_ratio`` (default 10%) of calls are
      admitted as probes; the rest short-circuit to the caller's fallback.
    """

    def __init__(
        self,
        *,
        failure_window: float = 10.0,
        probe_ratio: float = 0.1,
        probe_rng: Callable[[], float] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.failure_window = failure_window
        self.probe_ratio = probe_ratio
        self._probe_rng: Callable[[], float] = probe_rng or random.random
        self._failure_times: deque[float] = deque()

    def close(self) -> None:
        """Close the breaker and forget the failure window (fresh start)."""
        super().close()
        self._failure_times.clear()

    def _admit(self) -> str:
        """Decide whether the call may proceed. Must run under ``self._lock``.

        Returns the state the call should execute under (``closed`` or
        ``half-open``); raises :class:`pybreaker.CircuitBreakerError` when the
        call must be short-circuited to the fallback path.
        """
        state = self.state  # sync cached state with the storage
        if state.name == pybreaker.STATE_OPEN:
            opened_at = self._state_storage.opened_at
            timeout = timedelta(seconds=self.reset_timeout)
            if opened_at is None or datetime.now(UTC) < opened_at + timeout:
                raise pybreaker.CircuitBreakerError(
                    "Timeout not elapsed yet, circuit breaker still open"
                )
            self.half_open()
            state = self.state
        if state.name == pybreaker.STATE_HALF_OPEN and self._probe_rng() >= self.probe_ratio:
            raise HalfOpenProbeSkipped(
                "Half-open probe skipped (probe_ratio sampling)"
            )
        return state.name

    async def call_async(
        self, func: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        """Await ``func`` guarded by the breaker state machine."""
        with self._lock:
            self._admit()
            for listener in self.listeners:
                listener.before_call(self, func, *args, **kwargs)

        try:
            ret = await func(*args, **kwargs)
        except Exception as exc:
            self._handle_failure(exc)
            raise
        self._handle_success()
        return ret

    def _handle_failure(self, exc: Exception) -> None:
        with self._lock:
            if not self.is_system_error(exc):
                # Excluded (business) exception — counts as a success.
                self._handle_success()
                return

            now = time.monotonic()
            self._failure_times.append(now)
            cutoff = now - self.failure_window
            while self._failure_times and self._failure_times[0] < cutoff:
                self._failure_times.popleft()
            self._state_storage.increment_counter()

            for listener in self.listeners:
                listener.failure(self, exc)

            if self.current_state == pybreaker.STATE_HALF_OPEN:
                # Probe failed: back to open for another reset_timeout.
                self.open()
            elif (
                self.current_state == pybreaker.STATE_CLOSED
                and len(self._failure_times) >= self.fail_max
            ):
                self.open()

    def _handle_success(self) -> None:
        with self._lock:
            if self.current_state == pybreaker.STATE_HALF_OPEN:
                self._state_storage.increment_success_counter()
                if self._state_storage.success_counter >= self.success_threshold:
                    self.close()
            else:
                self._state_storage.reset_counter()

            for listener in self.listeners:
                listener.success(self)


class LogCircuitBreakerListener(pybreaker.CircuitBreakerListener):
    """Logs breaker state changes with the event names used across the codebase."""

    def __init__(self, component: str) -> None:
        self.component = component

    def state_change(
        self,
        cb: pybreaker.CircuitBreaker,
        old_state: pybreaker.CircuitBreakerState | None,
        new_state: pybreaker.CircuitBreakerState,
    ) -> None:
        previous = old_state.name if old_state else None
        if new_state.name == pybreaker.STATE_OPEN:
            logger.warning(
                "circuit_breaker_opened",
                component=self.component,
                previous=previous,
                fail_max=cb.fail_max,
                reset_timeout=cb.reset_timeout,
            )
        elif new_state.name == pybreaker.STATE_HALF_OPEN:
            logger.info(
                "circuit_breaker_half_open",
                component=self.component,
                previous=previous,
            )
        elif new_state.name == pybreaker.STATE_CLOSED:
            logger.info(
                "circuit_breaker_reset",
                component=self.component,
                previous=previous,
            )

    def failure(self, cb: pybreaker.CircuitBreaker, exc: BaseException) -> None:
        logger.warning(
            "circuit_breaker_failure",
            component=self.component,
            error=str(exc),
            state=cb.current_state,
        )

    def success(self, cb: pybreaker.CircuitBreaker) -> None:
        logger.debug("circuit_breaker_success", component=self.component)


class MetricsCircuitBreakerListener(pybreaker.CircuitBreakerListener):
    """Exports breaker state and failures to Prometheus."""

    def __init__(self, component: str) -> None:
        self.component = component
        scanner_circuit_breaker_state.labels(component=component).set(
            STATE_VALUES[pybreaker.STATE_CLOSED]
        )

    def state_change(
        self,
        cb: pybreaker.CircuitBreaker,
        old_state: pybreaker.CircuitBreakerState | None,
        new_state: pybreaker.CircuitBreakerState,
    ) -> None:
        scanner_circuit_breaker_state.labels(component=self.component).set(
            STATE_VALUES[new_state.name]
        )

    def failure(self, cb: pybreaker.CircuitBreaker, exc: BaseException) -> None:
        scanner_circuit_breaker_failures_total.labels(component=self.component).inc()

    def success(self, cb: pybreaker.CircuitBreaker) -> None:
        pass


def record_fallback(component: str, mode: str = "degrade", **fields: Any) -> None:
    """Count a fallback invocation (breaker short-circuit or upstream failure)."""
    scanner_circuit_breaker_fallback_invocations_total.labels(
        component=component, mode=mode
    ).inc()
    logger.debug(
        "circuit_breaker_fallback",
        component=component,
        mode=mode,
        **fields,
    )
