"""Circuit breaker for HashiCorp Vault PII tokenization (EP-14 / Prompt 38,
ADR-0008).

Unlike the vector DB breaker (``degrade``) and the ML classifier breaker
(per-tenant ``degrade`` / ``fail_closed``), the Vault breaker is **always**
fail-closed: PII policies require that raw PII never leaves the scanner
unminted, so any Vault unavailability — breaker open, half-open non-probe,
missing client or upstream error — raises
:class:`VaultUnavailableFailClosed` with reason
``vault_unavailable_cannot_redact``. Callers (the redactor, the proxy)
must BLOCK the request instead of forwarding it.

Built on the shared :class:`app.core.circuit_breaker.AsyncCircuitBreaker`
(same sliding 5-errors-in-10s window, 10% half-open probes, structlog and
Prometheus listeners as the other component breakers). The transport-level
breaker inside :class:`~app.core.redaction.vault_client.VaultClient` stays
in place; this module adds the component-level policy decision + metrics.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

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

COMPONENT = "vault"
VAULT_UNAVAILABLE_REASON = "vault_unavailable_cannot_redact"


class VaultUnavailableFailClosed(Exception):
    """Raised when Vault cannot mint/lookup a token and the policy is fail-closed.

    The caller must BLOCK the request and audit it with
    ``reason="vault_unavailable_cannot_redact"``.
    """

    def __init__(self, reason: str = VAULT_UNAVAILABLE_REASON, **context: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.context = context


class VaultOperationError(Exception):
    """Internal: a Vault encode/decode round trip failed (``VaultResult.ok=False``).

    Carries the client's error string so the breaker can record it as an
    upstream failure before the fail-closed fallback is applied.
    """

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error


class VaultCircuitBreaker:
    """Circuit breaker around :class:`VaultClient` encode/decode calls.

    Args:
        vault_client: Underlying client (its ``encode`` / ``decode``
            coroutines are guarded by the breaker); ``None`` resolves the
            process singleton at call time.
        fail_max: Failures inside ``failure_window`` that trip the breaker
            (defaults to ``Settings.vault_breaker_fail_max``).
        reset_timeout: Seconds in OPEN before half-open probing starts
            (defaults to ``Settings.vault_breaker_reset_timeout``).
        failure_window: Sliding window for the failure count (spec: 10s).
        probe_ratio: Share of traffic admitted as probes in half-open (10%).
        probe_rng: Injectable uniform [0, 1) sampler for deterministic tests.
    """

    component = COMPONENT

    def __init__(
        self,
        vault_client: Any = None,
        *,
        fail_max: Optional[int] = None,
        reset_timeout: Optional[float] = None,
        failure_window: float = 10.0,
        probe_ratio: float = 0.1,
        probe_rng: Optional[Callable[[], float]] = None,
    ) -> None:
        self._vault_client = vault_client
        self._breaker = AsyncCircuitBreaker(
            name=COMPONENT,
            fail_max=(
                fail_max if fail_max is not None else settings.vault_breaker_fail_max
            ),
            reset_timeout=(
                reset_timeout
                if reset_timeout is not None
                else settings.vault_breaker_reset_timeout
            ),
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

    def _get_vault_client(self) -> Any:
        """Get the injected client or the process singleton."""
        if self._vault_client is not None:
            return self._vault_client
        from app.core.redaction.vault_client import get_vault_client

        return get_vault_client()

    def _fallback(self, reason: str, **fields: Any) -> None:
        """Record the fallback and always raise fail-closed (ADR-0008)."""
        record_fallback(COMPONENT, mode="fail_closed", reason=reason, **fields)
        raise VaultUnavailableFailClosed(
            VAULT_UNAVAILABLE_REASON,
            component=COMPONENT,
            cause=reason,
            **fields,
        )

    async def encode(
        self,
        plaintext: str,
        *,
        context: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        """Tokenize *plaintext* behind the breaker; returns the raw Vault token.

        Always fail-closed: raises :class:`VaultUnavailableFailClosed` when
        the token cannot be minted (breaker open, half-open non-probe, client
        missing, or upstream error) — the caller must BLOCK the request.
        """
        client = self._get_vault_client()
        if client is None:
            self._fallback("vault_client_unavailable", tenant_id=tenant_id)

        async def _encode_or_raise() -> str:
            result = await client.encode(plaintext, context=context)
            if not result.ok:
                raise VaultOperationError(result.error or "vault_encode_failed")
            return result.value

        try:
            return await self._breaker.call_async(_encode_or_raise)
        except HalfOpenProbeSkipped:
            self._fallback("half_open_probe_skipped", tenant_id=tenant_id)
        except CircuitBreakerError:
            self._fallback("open", tenant_id=tenant_id)
        except VaultOperationError as exc:
            self._fallback(
                "upstream_error", tenant_id=tenant_id, error=exc.error
            )
        raise AssertionError("unreachable")  # pragma: no cover

    async def decode(
        self,
        token: str,
        *,
        context: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        """Reverse *token* behind the breaker; returns the original plaintext.

        Same fail-closed contract as :meth:`encode`.
        """
        client = self._get_vault_client()
        if client is None:
            self._fallback("vault_client_unavailable", tenant_id=tenant_id)

        async def _decode_or_raise() -> str:
            result = await client.decode(token, context=context)
            if not result.ok:
                raise VaultOperationError(result.error or "vault_decode_failed")
            return result.value

        try:
            return await self._breaker.call_async(_decode_or_raise)
        except HalfOpenProbeSkipped:
            self._fallback("half_open_probe_skipped", tenant_id=tenant_id)
        except CircuitBreakerError:
            self._fallback("open", tenant_id=tenant_id)
        except VaultOperationError as exc:
            self._fallback(
                "upstream_error", tenant_id=tenant_id, error=exc.error
            )
        raise AssertionError("unreachable")  # pragma: no cover


# Global singleton (wired in main startup, like set_vector_db_breaker)
_vault_breaker: Optional[VaultCircuitBreaker] = None


def get_vault_breaker() -> Optional[VaultCircuitBreaker]:
    return _vault_breaker


def set_vault_breaker(breaker: Optional[VaultCircuitBreaker]) -> None:
    global _vault_breaker
    _vault_breaker = breaker
