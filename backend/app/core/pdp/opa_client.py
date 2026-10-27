"""OPA client for the Policy Decision Point (EP-12, ADR-0004).

Responsibilities (ALPHA Prompt 28):
  - fetch the ACTIVE policy version from the bundle-service manifest
    (the version that OPA currently serves after its 30s bundle poll),
  - evaluate the PDP input document against the loaded Rego policies
    via the OPA Data API (GET /v1/data/<package>),
  - degrade gracefully: a small circuit breaker turns OPA errors into a
    ``None`` result so callers fall back to the local rules engine.

The client is async (httpx), has no shared mutable state beyond the breaker
counters, and is safe to use concurrently from the scan/proxy paths.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from app.config import settings
from app.core.metrics import scanner_pdp_opa_errors_total

try:  # OTel spans become real once Prompt 40 lands (opentelemetry deps)
    from opentelemetry import trace

    _tracer = trace.get_tracer(__name__)
except ImportError:  # pragma: no cover - no-op tracer until OTel is installed
    from contextlib import contextmanager

    class _NoopSpan:
        def set_attribute(self, *_args: Any, **_kwargs: Any) -> None:
            pass

    class _NoopTracer:
        @contextmanager
        def start_as_current_span(self, *_args: Any, **_kwargs: Any):
            yield _NoopSpan()

    _tracer = _NoopTracer()

logger = structlog.get_logger(__name__)


@dataclass
class OPADecision:
    """Result of evaluating the input document against the Rego package."""

    allowed: bool = True
    deny_reasons: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        """False when OPA could not be reached / returned garbage."""
        return self.error is None


class _CircuitBreaker:
    """Minimal async-friendly breaker (closed -> open -> half-open).

    Same semantics as the slow-path breaker: after ``fail_max`` consecutive
    failures the breaker opens for ``reset_timeout`` seconds and calls are
    short-circuited; then a half-open probe decides whether to close again.
    """

    def __init__(self, fail_max: int = 5, reset_timeout: float = 30.0):
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open = False

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._half_open:
            return "half_open"
        if time.monotonic() - self._opened_at >= self.reset_timeout:
            self._half_open = True
            return "half_open"
        return "open"

    def allow(self) -> bool:
        return self.state in ("closed", "half_open")

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._half_open = False

    def record_failure(self) -> None:
        self._failures += 1
        if self._half_open or self._failures >= self.fail_max:
            self._opened_at = time.monotonic()
            self._half_open = False


class OPAClient:
    """Async OPA Data API + bundle-service manifest client."""

    def __init__(
        self,
        opa_url: str | None = None,
        bundle_service_url: str | None = None,
        *,
        timeout_ms: int | None = None,
        package: str | None = None,
        version_ttl_seconds: int | None = None,
        fail_open: bool | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.opa_url = (opa_url if opa_url is not None else settings.opa_url).rstrip("/")
        self.bundle_service_url = (
            bundle_service_url if bundle_service_url is not None else settings.bundle_service_url
        ).rstrip("/")
        self.timeout = httpx.Timeout((timeout_ms if timeout_ms is not None else settings.opa_timeout_ms) / 1000)
        self.package = package if package is not None else settings.opa_policy_package
        self.version_ttl = (
            version_ttl_seconds
            if version_ttl_seconds is not None
            else settings.policy_version_cache_ttl_seconds
        )
        self.fail_open = fail_open if fail_open is not None else settings.pdp_opa_fail_open
        self._client = client or httpx.AsyncClient(timeout=self.timeout)
        self._breaker = _CircuitBreaker()
        self._version_cache: tuple[float, str] | None = None

    # -- active policy version -------------------------------------------------

    async def get_active_policy_version(self) -> str:
        """Fetch the active bundle version from the bundle-service manifest.

        Cached in-process for ``version_ttl`` seconds. Falls back to
        ``settings.policy_version`` when the bundle-service is unreachable —
        the audit log must never fail because of policy plumbing.
        """
        now = time.monotonic()
        if self._version_cache and now - self._version_cache[0] < self.version_ttl:
            return self._version_cache[1]

        version = settings.policy_version
        if self.bundle_service_url:
            try:
                resp = await self._client.get(f"{self.bundle_service_url}/bundles/manifest.json")
                resp.raise_for_status()
                version = str(resp.json().get("version") or version)
            except (httpx.HTTPError, ValueError) as exc:
                scanner_pdp_opa_errors_total.labels(type="manifest").inc()
                logger.warning("opa_manifest_fetch_failed", error=str(exc))
            else:
                self._version_cache = (now, version)
        else:
            self._version_cache = (now, version)
        return version

    def invalidate_version_cache(self) -> None:
        self._version_cache = None

    # -- policy evaluation ------------------------------------------------------

    async def evaluate(self, input_doc: dict) -> OPADecision:
        """Evaluate *input_doc* against ``GET /v1/data/<package>``.

        Returns an :class:`OPADecision` with ``ok=False`` on transport or
        decode errors (callers decide whether to fail open or closed);
        never raises.
        """
        if not self._breaker.allow():
            scanner_pdp_opa_errors_total.labels(type="circuit_open").inc()
            return OPADecision(error="circuit_open")

        data_path = self.package.replace(".", "/")
        url = f"{self.opa_url}/v1/data/{data_path}"

        with _tracer.start_as_current_span("pdp.opa.evaluate") as span:
            span.set_attribute("opa.url", url)
            span.set_attribute("opa.package", self.package)
            try:
                resp = await self._client.post(url, json={"input": input_doc})
                resp.raise_for_status()
                result = resp.json().get("result")
            except (httpx.HTTPError, ValueError) as exc:
                self._breaker.record_failure()
                scanner_pdp_opa_errors_total.labels(type="http").inc()
                logger.warning("opa_evaluate_failed", error=str(exc), breaker=self._breaker.state)
                span.set_attribute("error", True)
                return OPADecision(error=f"opa_unavailable: {exc}")

            if not isinstance(result, dict) or "allow" not in result:
                self._breaker.record_failure()
                scanner_pdp_opa_errors_total.labels(type="decode").inc()
                return OPADecision(error="unexpected_opa_result")

            self._breaker.record_success()
            deny_reasons = [str(m) for m in result.get("deny") or []]
            allowed = bool(result.get("allow")) and not deny_reasons
            span.set_attribute("opa.allowed", allowed)
            span.set_attribute("opa.deny_count", len(deny_reasons))
            return OPADecision(allowed=allowed, deny_reasons=deny_reasons)

    @property
    def breaker_state(self) -> str:
        return self._breaker.state

    async def close(self) -> None:
        await self._client.aclose()


# -- module-level singleton (wired in main.py startup, like set_shared_*) -----

_opa_client: OPAClient | None = None


def set_opa_client(client: OPAClient | None) -> None:
    global _opa_client
    _opa_client = client


def get_opa_client() -> OPAClient | None:
    return _opa_client


def init_opa_client() -> OPAClient | None:
    """Create the singleton when the OPA PDP is enabled; idempotent.

    Startup never fails because of OPA: with the PDP disabled (default) the
    scanner falls back to the local rules engine, and with it enabled the
    client degrades per-request via the circuit breaker.
    """
    if not settings.pdp_opa_enabled or not settings.opa_url:
        set_opa_client(None)
        logger.info("opa_pdp_disabled")
        return None
    client = OPAClient()
    set_opa_client(client)
    logger.info(
        "opa_pdp_enabled",
        opa_url=client.opa_url,
        bundle_service_url=client.bundle_service_url,
        package=client.package,
        fail_open=client.fail_open,
    )
    return client


async def close_opa_client() -> None:
    client = get_opa_client()
    if client is not None:
        await client.close()
    set_opa_client(None)
