"""Circuit breaker for the ML classifier (EP-14 / Prompt 37, ADR-0008).

Analogous to the vector DB breaker (:mod:`app.core.circuit_breakers.vector_db`)
with one difference: the fallback mode follows the tenant policy from
``Settings.tenant_policies``:

* ``default`` — ``degrade``: the breaker short-circuits to ``None`` and the
  verdict rests on the rules (fast path) only.
* ``strict`` — ``fail-closed``: the same situations raise
  :class:`MLUnavailableFailClosed` so the caller can BLOCK the request and
  audit it with reason ``ml_unavailable``.
"""

from __future__ import annotations

from typing import Any, Callable, Literal, Optional

import structlog
from pybreaker import CircuitBreakerError

from app.config import settings
from app.core.circuit_breaker import (
    AsyncCircuitBreaker,
    HalfOpenProbeSkipped,
    LogCircuitBreakerListener,
    MetricsCircuitBreakerListener,
    record_fallback,
)

logger = structlog.get_logger()

COMPONENT = "ml_classifier"
ML_UNAVAILABLE_REASON = "ml_unavailable"

FallbackMode = Literal["degrade", "fail_closed"]

# Tenant policy value -> breaker fallback mode (ADR-0008: per-policy fallback).
_TENANT_POLICY_MODES: dict[str, FallbackMode] = {
    "default": "degrade",
    "strict": "fail_closed",
}


class MLUnavailableFailClosed(Exception):
    """Raised when the classifier is unavailable and the tenant policy is strict.

    The caller must BLOCK the request and audit it with
    ``reason="ml_unavailable"``.
    """

    def __init__(self, reason: str = ML_UNAVAILABLE_REASON, **context: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.context = context


def resolve_fallback_mode(tenant_id: str | None) -> FallbackMode:
    """Map a tenant's policy (``Settings.tenant_policies``) to a fallback mode.

    Unknown or missing policies resolve to ``degrade`` (rule-only).
    """
    policy = settings.tenant_policies.get(
        tenant_id or settings.default_tenant_id, "default"
    )
    mode = _TENANT_POLICY_MODES.get(policy)
    if mode is None:
        logger.warning(
            "ml_cb_unknown_tenant_policy",
            tenant_id=tenant_id,
            policy=policy,
            resolved="degrade",
        )
        return "degrade"
    return mode


class MLClassifierCircuitBreaker:
    """Circuit breaker around :class:`PromptClassifier` inference calls.

    Args:
        classifier: Underlying classifier (its ``classify`` coroutine is
            guarded by the breaker); ``None`` resolves the shared singleton
            at call time.
        fail_max: Failures inside ``failure_window`` that trip the breaker.
        reset_timeout: Seconds in OPEN before half-open probing starts.
        failure_window: Sliding window for the failure count (spec: 10s).
        probe_ratio: Share of traffic admitted as probes in half-open (10%).
        probe_rng: Injectable uniform [0, 1) sampler for deterministic tests.
    """

    component = COMPONENT

    def __init__(
        self,
        classifier: Any = None,
        *,
        fail_max: int = 5,
        reset_timeout: float = 30.0,
        failure_window: float = 10.0,
        probe_ratio: float = 0.1,
        probe_rng: Optional[Callable[[], float]] = None,
    ) -> None:
        self._classifier = classifier
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

    def _get_classifier(self) -> Any:
        """Get the injected classifier or the shared singleton."""
        if self._classifier is not None:
            return self._classifier
        from app.core.detectors.ml_classifier import get_shared_classifier

        return get_shared_classifier()

    def _fallback(
        self,
        mode: FallbackMode,
        reason: str,
        tenant_id: str | None,
        **fields: Any,
    ) -> None:
        """Apply the tenant's fallback mode: raise (strict) or degrade (default)."""
        record_fallback(COMPONENT, mode=mode, reason=reason, **fields)
        if mode == "fail_closed":
            raise MLUnavailableFailClosed(
                ML_UNAVAILABLE_REASON,
                component=COMPONENT,
                cause=reason,
                tenant_id=tenant_id,
            )

    async def classify(
        self, text: str, *, tenant_id: str | None = None
    ) -> Optional[Any]:
        """Classify *text* behind the breaker.

        Returns the classification result, or ``None`` when degraded to
        rule-only mode (breaker open, half-open non-probe, classifier
        unavailable or upstream failure) under the ``default`` policy. Under
        the ``strict`` policy the same situations raise
        :class:`MLUnavailableFailClosed`.
        """
        mode = resolve_fallback_mode(tenant_id)
        classifier = self._get_classifier()
        if classifier is None:
            self._fallback(mode, "classifier_unavailable", tenant_id)
            return None

        try:
            return await self._breaker.call_async(classifier.classify, text)
        except HalfOpenProbeSkipped:
            self._fallback(mode, "half_open_probe_skipped", tenant_id)
            return None
        except CircuitBreakerError:
            self._fallback(mode, "open", tenant_id)
            return None
        except Exception as exc:  # noqa: BLE001 — degrade, never break the caller
            self._fallback(
                mode, "upstream_error", tenant_id, error=str(exc)
            )
            return None


# Global singleton (wired in main.init_post_factum_stack)
_ml_classifier_breaker: Optional[MLClassifierCircuitBreaker] = None


def get_ml_classifier_breaker() -> Optional[MLClassifierCircuitBreaker]:
    return _ml_classifier_breaker


def set_ml_classifier_breaker(
    breaker: Optional[MLClassifierCircuitBreaker],
) -> None:
    global _ml_classifier_breaker
    _ml_classifier_breaker = breaker
