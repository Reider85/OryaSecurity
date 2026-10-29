"""Vault-based PII tokenization for the outbound LLM path (EP-13/Prompt 32,
ADR-0007).

Differences from the legacy audit redactor (``app.core.redactor``):

* that one replaces PII with *irreversible* ``<SSN_HASH_...>`` placeholders
  for audit storage — this one mints *reversible* ``<SSN_T_<vault-token>>``
  tokens so the LLM never sees raw PII and Prompt 33 (detokenizer) can map
  the token back for the end user;
* detection runs inside the redactor (``PIIDetector``, regex now, Presidio
  NER via hook in Prompt 35) instead of relying on precomputed matches.

Fail-closed contract (ADR-0008): when Vault is *configured* but a token
cannot be minted, ``redact()`` returns ``error != None`` and an **empty**
``redacted_text`` — callers must never forward the original text in that
case. When Vault is not configured at all (``vault_url`` empty, the ALPHA
default) the redactor is a passthrough no-op.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

from app.core.detectors.pii import PIIDetector
from app.core.metrics import (
    scanner_redaction_failures_total,
    scanner_redaction_tokens_total,
)
from app.core.redactor import _label_for
from app.core.redaction.vault_client import VaultClient, get_vault_client
from app.core.rules.base import RuleMatch

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

VAULT_UNAVAILABLE_REASON = "vault_unavailable_cannot_redact"


@dataclass(frozen=True)
class TokenInfo:
    """Metadata about one minted token (never contains the raw value)."""

    token: str
    type: str  # LABEL: SSN, EMAIL, AWS_KEY, ...
    original_hash: str  # sha256(value)[:16], same scheme as rules._hash_value
    position: tuple[int, int]  # span in the *original* text


@dataclass
class RedactedText:
    """Result of tokenizing a single text."""

    redacted_text: str
    tokens: list[TokenInfo] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class RedactionOutcome:
    """Aggregated result of tokenizing a list of chat messages.

    Messages are mutated in place, so there is no single redacted text;
    only the minted tokens and the (fail-closed) error matter.
    """

    tokens: list[TokenInfo] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _hash_value(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _resolve_spans(matches: list[RuleMatch]) -> list[RuleMatch]:
    """Drop matches that overlap a previously accepted span.

    Same semantics as the legacy audit redactor: matches sorted by start
    position, a fully contained / overlapping span is discarded so each
    character of the text is replaced at most once.
    """
    accepted: list[RuleMatch] = []
    for match in sorted(matches, key=lambda m: m.position[0]):
        start, end = match.position
        if end <= start or start < 0:
            continue
        if any(start < a_end and a_start < end for a_start, a_end in
               ((m.position[0], m.position[1]) for m in accepted)):
            continue
        accepted.append(match)
    return accepted


class PIIRedactor:
    """Detect PII/secrets and replace them with reversible Vault tokens.

    Args:
        vault_client: client used for encode/decode; ``None`` disables
            tokenization (passthrough). Defaults to the process singleton
            wired in ``app.main`` startup.
        detector: PII detector; defaults to :func:`make_detector` (Prompt 35):
            YAML regex rules plus Presidio NER when ``presidio_enabled``,
            regex-only otherwise.
    """

    def __init__(
        self,
        vault_client: Optional[VaultClient] = None,
        detector: Optional[PIIDetector] = None,
    ):
        self.vault_client = vault_client
        if detector is None:
            # Local import: detectors.pii_ner reaches back into this package
            # at module level (circular if imported at load time).
            from app.core.detectors.pii_ner import make_detector

            detector = make_detector()
        self.detector = detector

    async def redact(self, text: str, tenant_id: str | None = None) -> RedactedText:
        """Tokenize *text*: ``"my ssn 123-45-6789"`` -> ``"my ssn <SSN_T_...>"``.

        Never raises. On Vault failure returns ``ok=False`` with an empty
        ``redacted_text`` (fail-closed — see module docstring).
        """
        with _tracer.start_as_current_span("pii.redact") as span:
            span.set_attribute("scanner.text_len", len(text or ""))
            if not text:
                return RedactedText(redacted_text=text or "")

            matches = self.detector.detect(text)
            if not matches:
                span.set_attribute("scanner.tokens", 0)
                return RedactedText(redacted_text=text, tokens=[])

            if self.vault_client is None:
                # Vault not configured: redaction is disabled, passthrough.
                span.set_attribute("scanner.redaction", "disabled")
                logger.debug(
                    "redaction_disabled",
                    matches=len(matches),
                )
                return RedactedText(redacted_text=text, tokens=[])

            spans = _resolve_spans(matches)
            start_time = time.monotonic()

            # Mint one token per unique (value, label) pair; the same SSN
            # appearing twice costs a single Vault round-trip and yields the
            # same token (convergent encryption on the Vault side).
            unique: list[tuple[str, str, RuleMatch]] = []
            seen: set[tuple[str, str]] = set()
            for match in spans:
                value = text[match.position[0] : match.position[1]]
                label = _label_for(match.rule_id)
                key = (value, label)
                if key in seen:
                    continue
                seen.add(key)
                unique.append((value, label, match))

            results = await asyncio.gather(
                *(self.vault_client.encode(value, context=tenant_id) for value, _, _ in unique)
            )

            value_to_token: dict[tuple[str, str], str] = {}
            for (value, label, match), result in zip(unique, results):
                if not result.ok:
                    error = result.error or "vault_encode_failed"
                    scanner_redaction_failures_total.labels(error=error).inc()
                    span.set_attribute("error", True)
                    span.set_attribute("scanner.error", error)
                    logger.warning(
                        "redaction_encode_failed",
                        error=error,
                        type=label,
                        engine=result.engine or None,
                    )
                    # Fail-closed: empty text, no tokens — caller must block.
                    return RedactedText(redacted_text="", tokens=[], error=error)
                value_to_token[(value, label)] = f"<{label}_T_{result.value}>"

            tokens: list[TokenInfo] = []
            redacted = text
            # Right-to-left replacement keeps earlier positions valid.
            for match in reversed(spans):
                start, end = match.position
                value = text[start:end]
                label = _label_for(match.rule_id)
                token = value_to_token[(value, label)]
                redacted = redacted[:start] + token + redacted[end:]
                tokens.append(
                    TokenInfo(
                        token=token,
                        type=label,
                        original_hash=_hash_value(value),
                        position=(start, end),
                    )
                )
            tokens.reverse()

            duration_ms = (time.monotonic() - start_time) * 1000
            for token_info in tokens:
                scanner_redaction_tokens_total.labels(type=token_info.type).inc()

            span.set_attribute("scanner.tokens", len(tokens))
            logger.info(
                "prompt_tokenized",
                token_count=len(tokens),
                types=[t.type for t in tokens],
                duration_ms=round(duration_ms, 2),
                tenant_id=tenant_id,
            )
            return RedactedText(redacted_text=redacted, tokens=tokens)


async def redact_messages_for_llm(
    messages: list[Any],
    tenant_id: str | None = None,
    *,
    vault_client: Optional[VaultClient] = None,
    detector: Optional[PIIDetector] = None,
) -> RedactionOutcome:
    """Tokenize every non-empty message content in place (outbound proxy path).

    *messages* are ``ChatCompletionMessage``-like pydantic models; their
    ``content`` attribute is replaced with the tokenized text. Returns a
    fail-closed :class:`RedactionOutcome` — on ``ok=False`` the caller must
    block the request instead of forwarding it.
    """
    if vault_client is None:
        vault_client = get_vault_client()
    redactor = PIIRedactor(vault_client=vault_client, detector=detector)

    outcome = RedactionOutcome()
    for message in messages:
        content = getattr(message, "content", None)
        if not content:
            continue
        result = await redactor.redact(content, tenant_id)
        if not result.ok:
            return RedactionOutcome(tokens=[], error=result.error)
        if result.redacted_text != content:
            message.content = result.redacted_text
        outcome.tokens.extend(result.tokens)

    if outcome.tokens:
        logger.info(
            "messages_tokenized",
            message_count=len(messages),
            token_count=len(outcome.tokens),
            tenant_id=tenant_id,
        )
    return outcome
