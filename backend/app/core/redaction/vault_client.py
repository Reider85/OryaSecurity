"""Vault client for PII tokenization (EP-13/Prompt 31, ADR-0007).

Responsibilities:
  - probe cluster health via ``GET /v1/sys/health`` (init / seal / standby),
  - encode PII into reversible tokens and decode them back,
  - auto-detect the secrets engine: the Tokenization engine (Vault Enterprise)
    or the open-source ``transit`` engine with convergent encryption as a
    fallback (same plaintext -> same token, see docs/vault-setup.md),
  - degrade gracefully: a small circuit breaker turns Vault errors into a
    failed result so callers (redactor, later CB per ADR-0008) can decide
    fail-closed semantics without the client ever raising.

The client is async (httpx), has no shared mutable state beyond the breaker
counters and the engine probe cache, and is safe to use concurrently.

Module-level singleton is wired in ``app.main`` startup, like ``OPAClient``.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from app.config import settings

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

_ENGINE_TOKENIZATION = "tokenization"
_ENGINE_TRANSIT = "transit"


@dataclass
class VaultHealth:
    """Parsed ``GET /v1/sys/health`` response."""

    reachable: bool = False
    initialized: bool = False
    sealed: bool = True
    standby: bool = False
    active: bool = False
    cluster_name: str | None = None
    error: str | None = None

    @property
    def ready(self) -> bool:
        """True when the node is initialized, unsealed and active."""
        return self.reachable and self.initialized and not self.sealed and self.active


@dataclass
class VaultResult:
    """Outcome of an encode (token) / decode (plaintext) round trip step."""

    ok: bool = False
    value: str = ""
    error: str | None = None
    engine: str = ""

    @property
    def token(self) -> str:
        """Alias for encode results (``value`` holds the token)."""
        return self.value


class _CircuitBreaker:
    """Minimal async-friendly breaker (closed -> open -> half-open).

    Same semantics as the OPA/slow-path breakers: after ``fail_max``
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


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _b64decode(value: str) -> str:
    return base64.b64decode(value.encode("ascii")).decode("utf-8")


class VaultClient:
    """Async Vault HTTP API client (tokenization / transit)."""

    def __init__(
        self,
        vault_url: str | None = None,
        token: str | None = None,
        *,
        mount: str | None = None,
        key_name: str | None = None,
        engine: str | None = None,
        namespace: str | None = None,
        timeout_ms: int | None = None,
        fail_max: int | None = None,
        reset_timeout: float | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.vault_url = (vault_url if vault_url is not None else settings.vault_url).rstrip("/")
        self.token = token if token is not None else settings.vault_token
        self.mount = (mount if mount is not None else settings.vault_secret_mount).strip("/")
        self.key_name = key_name if key_name is not None else settings.vault_key_name
        self.engine = (engine if engine is not None else settings.vault_engine).lower()
        self.namespace = namespace if namespace is not None else settings.vault_namespace
        timeout = (timeout_ms if timeout_ms is not None else settings.vault_timeout_ms) / 1000
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout))
        self._breaker = _CircuitBreaker(
            fail_max=fail_max if fail_max is not None else settings.vault_breaker_fail_max,
            reset_timeout=(
                reset_timeout if reset_timeout is not None else settings.vault_breaker_reset_timeout
            ),
        )
        self._resolved_engine: str | None = None if self.engine == "auto" else self.engine

    # -- helpers --------------------------------------------------------------

    @property
    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.token:
            headers["X-Vault-Token"] = self.token
        if self.namespace:
            headers["X-Vault-Namespace"] = self.namespace
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
    ) -> tuple[int, dict]:
        """Perform a request; returns (status_code, parsed_json_or_empty).

        Transport errors are folded into a ``(0, {})`` result — the caller
        records the breaker failure. Never raises.
        """
        url = f"{self.vault_url}{path}"
        try:
            resp = await self._client.request(method, url, json=json_body, headers=self._headers)
        except httpx.HTTPError as exc:
            logger.warning("vault_request_failed", method=method, path=path, error=str(exc))
            return 0, {}
        try:
            return resp.status_code, resp.json() if resp.content else {}
        except ValueError:
            return resp.status_code, {}

    # -- health ---------------------------------------------------------------

    async def get_health(self) -> VaultHealth:
        """Probe ``GET /v1/sys/health`` (unauthenticated by design).

        Status codes: 200 active+unsealed, 429 standby, 472 disaster-recovery
        mode, 473 performance standby, 501 not initialized, 503 sealed.
        """
        code, body = await self._request("GET", "/v1/sys/health")
        if code == 0:
            return VaultHealth(error="vault_unreachable")
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        # /v1/sys/health returns seal status fields at the top level
        merged = {**body, **data}
        return VaultHealth(
            reachable=True,
            initialized=bool(merged.get("initialized", False)),
            sealed=bool(merged.get("sealed", True)),
            standby=bool(merged.get("standby", False)),
            active=code == 200,
            cluster_name=merged.get("cluster_name"),
            error=None,
        )

    # -- engine resolution ----------------------------------------------------

    async def _resolve_engine(self) -> str | None:
        """Detect the engine mounted at ``self.mount`` (auto mode).

        Returns the engine name, or ``None`` when it cannot be determined
        (missing mount / no permissions) — the caller reports the error.
        Cached after the first successful probe.
        """
        if self._resolved_engine is not None:
            return self._resolved_engine

        code, body = await self._request("GET", f"/v1/sys/mounts/{self.mount}")
        if code == 0:
            return None  # transport error — caller records breaker failure
        if code == 404:
            self._breaker.record_failure()
            return None
        if code < 200 or code >= 300:
            self._breaker.record_failure()
            logger.warning(
                "vault_mount_probe_failed",
                mount=self.mount,
                status=code,
            )
            return None

        self._breaker.record_success()
        data = body.get("data") or {}
        mount_type = str(data.get("type", "")).rstrip("/")
        if mount_type.endswith(_ENGINE_TOKENIZATION):
            resolved = _ENGINE_TOKENIZATION
        elif mount_type.endswith(_ENGINE_TRANSIT):
            resolved = _ENGINE_TRANSIT
        else:
            logger.warning("vault_unknown_engine", mount=self.mount, type=mount_type)
            return None
        self._resolved_engine = resolved
        logger.info("vault_engine_resolved", mount=self.mount, engine=resolved)
        return resolved

    async def resolve_engine(self) -> str | None:
        """Public engine auto-detection (``auto`` mode), cached after first probe.

        Same as the internal helper used by encode/decode; exposed for
        maintenance flows (Prompt 34 TTL cleanup) that need to know which
        secrets engine they are rotating.
        """
        return await self._resolve_engine()

    # -- encode / decode ------------------------------------------------------

    async def encode(self, plaintext: str, *, context: str | None = None) -> VaultResult:
        """Tokenize *plaintext* into an opaque/reversible token.

        ``context`` scopes the token (tenant id in BETA, ADR-0011); it is
        required for the transit+convergent path so the same value under a
        different tenant maps to a different ciphertext.
        """
        with _tracer.start_as_current_span("vault.encode") as span:
            span.set_attribute("vault.mount", self.mount)
            if not self._breaker.allow():
                span.set_attribute("error", True)
                return VaultResult(error="circuit_open")

            engine = await self._resolve_engine()
            if engine is None:
                span.set_attribute("error", True)
                return VaultResult(error="vault_engine_unavailable")
            span.set_attribute("vault.engine", engine)

            if engine == _ENGINE_TOKENIZATION:
                path = f"/v1/{self.mount}/encode/{self.key_name}"
                body: dict = {"plaintext": _b64(plaintext)}
                token_field = "token"
            else:  # transit
                path = f"/v1/{self.mount}/encrypt/{self.key_name}"
                body = {
                    "plaintext": _b64(plaintext),
                    "convergent_encryption": True,
                    "context": _b64(context or "default"),
                }
                token_field = "ciphertext"

            code, resp = await self._request("POST", path, json_body=body)
            if code == 0 or code < 200 or code >= 300:
                self._breaker.record_failure()
                span.set_attribute("error", True)
                logger.warning("vault_encode_failed", engine=engine, status=code)
                return VaultResult(
                    error=_vault_error(resp, code, default="vault_encode_failed"),
                    engine=engine,
                )

            self._breaker.record_success()
            token = str((resp.get("data") or {}).get(token_field) or "")
            if not token:
                span.set_attribute("error", True)
                return VaultResult(error="vault_empty_token", engine=engine)
            span.set_attribute("vault.token_len", len(token))
            return VaultResult(ok=True, value=token, engine=engine)

    async def decode(self, token: str, *, context: str | None = None) -> VaultResult:
        """Reverse *token* back into the original value."""
        with _tracer.start_as_current_span("vault.decode") as span:
            span.set_attribute("vault.mount", self.mount)
            if not self._breaker.allow():
                span.set_attribute("error", True)
                return VaultResult(error="circuit_open")

            engine = await self._resolve_engine()
            if engine is None:
                span.set_attribute("error", True)
                return VaultResult(error="vault_engine_unavailable")
            span.set_attribute("vault.engine", engine)

            if engine == _ENGINE_TOKENIZATION:
                path = f"/v1/{self.mount}/decode/{self.key_name}"
                body: dict = {"token": token}
            else:
                path = f"/v1/{self.mount}/decrypt/{self.key_name}"
                body = {"ciphertext": token, "context": _b64(context or "default")}

            code, resp = await self._request("POST", path, json_body=body)
            if code == 0 or code < 200 or code >= 300:
                self._breaker.record_failure()
                span.set_attribute("error", True)
                logger.warning("vault_decode_failed", engine=engine, status=code)
                return VaultResult(
                    error=_vault_error(resp, code, default="vault_decode_failed"),
                    engine=engine,
                )

            self._breaker.record_success()
            plaintext_b64 = str((resp.get("data") or {}).get("plaintext") or "")
            if not plaintext_b64:
                span.set_attribute("error", True)
                return VaultResult(error="vault_empty_plaintext", engine=engine)
            try:
                value = _b64decode(plaintext_b64)
            except (ValueError, UnicodeDecodeError):
                span.set_attribute("error", True)
                return VaultResult(error="vault_bad_plaintext", engine=engine)
            return VaultResult(ok=True, value=value, engine=engine)

    # -- misc -----------------------------------------------------------------

    async def api(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
    ) -> tuple[int, dict]:
        """Generic authenticated Vault API call (Prompt 34, key management).

        Reuses the client's URL/token/namespace/timeout so admin-level
        operations (key rotation, ``min_decryption_version`` config) do not
        need their own transport. Deliberately does **not** touch the circuit
        breaker: those calls are maintenance, not the PII hot path.
        """
        return await self._request(method, path, json_body=json_body)

    @property
    def breaker_state(self) -> str:
        return self._breaker.state

    @property
    def resolved_engine(self) -> str | None:
        return self._resolved_engine

    async def close(self) -> None:
        await self._client.aclose()


def _vault_error(body: dict, code: int, *, default: str) -> str:
    """Extract Vault's ``errors`` list into a compact single-line reason."""
    if code == 0:
        return "vault_unreachable"
    errors = body.get("errors")
    if isinstance(errors, list) and errors:
        return f"{default}: " + "; ".join(str(e) for e in errors)[:300]
    return default


# -- module-level singleton (wired in main.py startup, like set_shared_*) -----

_vault_client: VaultClient | None = None


def set_vault_client(client: VaultClient | None) -> None:
    global _vault_client
    _vault_client = client


def get_vault_client() -> VaultClient | None:
    return _vault_client


def init_vault_client() -> VaultClient | None:
    """Create the singleton when Vault is configured; idempotent.

    Startup never fails because of Vault: with an empty ``vault_url`` (default)
    the scanner simply has no redaction client, and with it configured the
    client degrades per-request via the circuit breaker.
    """
    if not settings.vault_url:
        set_vault_client(None)
        logger.info("vault_redaction_disabled")
        return None
    client = VaultClient()
    set_vault_client(client)
    logger.info(
        "vault_redaction_enabled",
        vault_url=client.vault_url,
        mount=client.mount,
        engine=client.engine,
        has_token=bool(client.token),
        namespace=client.namespace,
    )
    return client


async def close_vault_client() -> None:
    client = get_vault_client()
    if client is not None:
        await client.close()
    set_vault_client(None)
