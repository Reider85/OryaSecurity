"""Reverse tokenization: Vault tokens back to original values (EP-13/Prompt 33,
ADR-0007).

The outbound path (Prompt 32's ``PIIRedactor``) replaces PII/secrets with
reversible ``<TYPE_T_<vault-token>>`` tokens before the prompt reaches the LLM.
The LLM frequently echoes those tokens back in its response — this module
restores them so the end user sees the original value:

    "Your SSN <SSN_T_vt1> is ..."  ->  "Your SSN 123-45-6789 is ..."

Contract differences from the redactor (deliberate, see ADR-0008):

* *outbound* redaction is fail-closed — a token that cannot be minted blocks
  the request so raw PII never reaches the LLM;
* *inbound* detokenization is fail-open — a token that cannot be decoded is
  left in the text as-is (a visible placeholder is safe; it is not PII), the
  failure is logged and counted, and the response is never blocked or raised.

When Vault is not configured (``vault_url`` empty, the ALPHA default) the
detokenizer is a passthrough no-op, mirroring the redactor.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Optional

import structlog

from app.core.metrics import (
    scanner_detokenization_failures_total,
    scanner_detokenization_tokens_total,
)
from app.core.redaction.vault_client import VaultClient, get_vault_client

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

# Minted by the redactor as ``f"<{label}_T_{vault_token}>"`` (redactor.py).
# Non-greedy type so a vault token that itself contains "_T_" still splits on
# the *first* separator. Deliberately does not match the legacy audit
# placeholders ``<SSN_HASH_...>`` (irreversible) or ``<REDACTED>``.
TOKEN_RE = re.compile(r"<([A-Z][A-Z0-9_]*?)_T_([^<>]+)>")

# A streaming suffix starting at an unclosed "<" that could still grow into a
# token: either the type part ("<SSN", "<AWS_KEY") or the id part
# ("<SSN_T_vt1"). Anything else ("<div", "<3") is literal text — emit now.
_PARTIAL_TYPE_RE = re.compile(r"^<[A-Z][A-Z0-9_]*$")
_PARTIAL_ID_RE = re.compile(r"^<[A-Z][A-Z0-9_]+_T_[^<>]*$")

# Cap on how much text a streaming detokenizer may hold back waiting for a
# token to close. Past this a "<" is treated as literal (bounded memory, and
# no legitimately long "<...>" markup gets stuck in the buffer).
DEFAULT_MAX_HOLD = 256


def find_tokens(text: str) -> list[tuple[str, str, str]]:
    """Return ``(full_match, type, vault_token)`` for every minted token."""
    return [(m.group(0), m.group(1), m.group(2)) for m in TOKEN_RE.finditer(text)]


async def _decode_unique(
    vault_client: VaultClient,
    vault_tokens: list[str],
    tenant_id: str | None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Decode each distinct vault token once, concurrently.

    Returns ``(values, errors)``: vault token -> original value, and vault
    token -> error reason for the failed ones. Fail-open: a failed lookup
    never raises, the caller keeps the original token.
    """
    if not vault_tokens:
        return {}, {}

    async def _one(token: str) -> tuple[str, str | None, str | None]:
        result = await vault_client.decode(token, context=tenant_id)
        if not result.ok:
            return token, None, result.error or "detokenize_lookup_failed"
        return token, result.value, None

    triples = await asyncio.gather(*(_one(t) for t in vault_tokens))
    values: dict[str, str] = {}
    errors: dict[str, str] = {}
    for token, value, error in triples:
        if error is None and value is not None:
            values[token] = value
        else:
            errors[token] = error or "detokenize_lookup_failed"
    return values, errors


class PIIDetokenizer:
    """Restore Vault-minted ``<TYPE_T_...>`` tokens back to original values.

    Args:
        vault_client: client used for decode; ``None`` resolves the process
            singleton wired in ``app.main`` startup (which itself is ``None``
            when Vault is not configured -> passthrough).
    """

    def __init__(self, vault_client: Optional[VaultClient] = None):
        self.vault_client = vault_client

    def _resolve_client(self) -> VaultClient | None:
        if self.vault_client is not None:
            return self.vault_client
        return get_vault_client()

    async def detokenize(self, text_with_tokens: str, tenant_id: str | None = None) -> str:
        """Replace every ``<TYPE_T_...>`` token with its original value.

        Never raises. On Vault failure the token stays in the text (fail-open
        — see module docstring).
        """
        with _tracer.start_as_current_span("pii.detokenize") as span:
            span.set_attribute("scanner.text_len", len(text_with_tokens or ""))
            if not text_with_tokens:
                return text_with_tokens or ""

            found = find_tokens(text_with_tokens)
            if not found:
                span.set_attribute("scanner.tokens", 0)
                return text_with_tokens

            vault_client = self._resolve_client()
            if vault_client is None:
                # Vault not configured: redaction is disabled, nothing to restore.
                span.set_attribute("scanner.detokenization", "disabled")
                logger.debug(
                    "detokenization_disabled",
                    tokens=len(found),
                )
                return text_with_tokens

            start_time = time.monotonic()
            # One round-trip per distinct vault token; a repeated <SSN_T_vt1>
            # decodes once and maps to the same value everywhere.
            unique_vault_tokens = list(dict.fromkeys(vault_token for _, _, vault_token in found))
            decoded, vault_errors = await _decode_unique(
                vault_client, unique_vault_tokens, tenant_id
            )

            restored = text_with_tokens
            token_count = 0
            failure_count = 0
            # Right-to-left keeps earlier positions valid while replacing.
            for full_match, label, vault_token in reversed(found):
                value = decoded.get(vault_token)
                if value is None:
                    failure_count += 1
                    error = vault_errors.get(vault_token) or "detokenize_lookup_failed"
                    scanner_detokenization_failures_total.labels(error=error).inc()
                    span.set_attribute("error", True)
                    span.set_attribute("scanner.error", error)
                    logger.warning(
                        "detokenize_token_failed",
                        type=label,
                        tenant_id=tenant_id,
                    )
                    continue
                scanner_detokenization_tokens_total.labels(type=label).inc()
                token_count += 1
                idx = restored.rfind(full_match)
                if idx == -1:  # pragma: no cover - defensive; finditer saw it
                    continue
                restored = restored[:idx] + value + restored[idx + len(full_match):]

            duration_ms = (time.monotonic() - start_time) * 1000
            span.set_attribute("scanner.tokens", token_count)
            span.set_attribute("scanner.failures", failure_count)
            logger.info(
                "response_detokenized",
                token_count=token_count,
                failure_count=failure_count,
                duration_ms=round(duration_ms, 2),
                tenant_id=tenant_id,
            )
            return restored


async def detokenize_text(
    text: str,
    tenant_id: str | None = None,
    *,
    vault_client: Optional[VaultClient] = None,
) -> str:
    """Module-level helper: detokenize with the process singleton client."""
    detokenizer = PIIDetokenizer(
        vault_client=vault_client if vault_client is not None else get_vault_client()
    )
    return await detokenizer.detokenize(text, tenant_id=tenant_id)


class StreamingDetokenizer:
    """Chunk-wise reverse tokenization for SSE streams (ADR-0007).

    A token can be split across upstream chunks (``"<SSN_T_vau"`` +
    ``"lt-token-42>"``), so raw per-chunk replacement would miss it. This
    buffer holds back only the trailing suffix that could still become a
    token; everything else is decoded and emitted immediately, keeping the
    stream live.

    ``flush()`` must be called when the stream ends: complete tokens are
    decoded, an unclosed remainder is emitted verbatim (a placeholder in the
    output is safe — it is not PII).
    """

    def __init__(
        self,
        vault_client: Optional[VaultClient] = None,
        tenant_id: str | None = None,
        max_hold: int = DEFAULT_MAX_HOLD,
    ):
        self.detokenizer = PIIDetokenizer(vault_client=vault_client)
        self.tenant_id = tenant_id
        self.max_hold = max(1, max_hold)
        self._buffer = ""

    def _split_tail(self, buffer: str) -> tuple[str, str]:
        """Split *buffer* into (emittable prefix, held partial-token suffix).

        The suffix is held only when an unclosed "<" starts a sequence that
        could still match ``TOKEN_RE``; otherwise everything is emittable.
        """
        lt = buffer.rfind("<")
        if lt == -1:
            return buffer, ""

        suffix = buffer[lt:]
        # A ">" after the last "<" means any token in it is already complete
        # (TOKEN_RE cannot span ">"), so nothing is ambiguous — but a trailing
        # "<..." without ">" may still be an opening token. Re-check from the
        # last "<" that has no ">" after it.
        gt = buffer.rfind(">")
        open_lt = lt if gt < lt else -1
        if open_lt == -1:
            return buffer, ""

        suffix = buffer[open_lt:]
        if len(suffix) > self.max_hold:
            # Too long to plausibly be a token — treat as literal text.
            return buffer, ""
        if _PARTIAL_TYPE_RE.match(suffix) or _PARTIAL_ID_RE.match(suffix):
            return buffer[:open_lt], suffix
        return buffer, ""

    async def feed(self, delta: str) -> str:
        """Consume one streamed chunk; return the text safe to send now."""
        if not delta:
            return ""
        self._buffer += delta
        prefix, self._buffer = self._split_tail(self._buffer)
        if not prefix:
            return ""
        return await self.detokenizer.detokenize(prefix, tenant_id=self.tenant_id)

    async def flush(self) -> str:
        """End of stream: decode what is buffered and release the tail."""
        if not self._buffer:
            return ""
        pending, self._buffer = self._buffer, ""
        return await self.detokenizer.detokenize(pending, tenant_id=self.tenant_id)

    def discard(self) -> None:
        """Drop the held tail without emitting it.

        For callers that re-send the whole accumulated text afterwards (the
        v1 stream's final finish_reason chunk): the tail is already contained
        there, flushing it again would duplicate output.
        """
        self._buffer = ""
