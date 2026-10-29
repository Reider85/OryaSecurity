"""Unit tests for the Vault circuit breaker (EP-14 / Prompt 38, ADR-0008).

Covers the closed/open/half-open transitions on ``VaultClient.encode`` /
``decode``, the **always fail-closed** fallback (BLOCK with reason
``vault_unavailable_cannot_redact``), the Prometheus metrics and the
PIIRedactor integration.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from prometheus_client import REGISTRY

from app.core.circuit_breakers.vault import (
    COMPONENT,
    VAULT_UNAVAILABLE_REASON,
    VaultCircuitBreaker,
    VaultUnavailableFailClosed,
    get_vault_breaker,
    set_vault_breaker,
)
from app.core.redaction.vault_client import VaultResult
from app.core.rules.base import RuleMatch

STATE_METRIC = "scanner_circuit_breaker_state"
FAILURES_METRIC = "scanner_circuit_breaker_failures_total"
FALLBACK_METRIC = "scanner_circuit_breaker_fallback_invocations_total"


def _vault_client(ok: bool = True) -> AsyncMock:
    client = AsyncMock()
    if ok:
        client.encode.return_value = VaultResult(
            ok=True, value="vt-abc", engine="tokenization"
        )
        client.decode.return_value = VaultResult(
            ok=True, value="123-45-6789", engine="tokenization"
        )
    else:
        client.encode.return_value = VaultResult(ok=False, error="vault_unreachable")
        client.decode.return_value = VaultResult(ok=False, error="vault_unreachable")
    return client


def _vault_breaker(
    client: AsyncMock | None = None, **kwargs
) -> VaultCircuitBreaker:
    kwargs.setdefault("probe_rng", lambda: 0.0)  # always win the probe draw
    return VaultCircuitBreaker(client or _vault_client(), **kwargs)


def _elapsed_reset_timeout(breaker: VaultCircuitBreaker) -> None:
    """Backdate opened_at so the OPEN cooldown has elapsed."""
    breaker._breaker._state_storage.opened_at = datetime.now(UTC) - timedelta(
        seconds=breaker._breaker.reset_timeout + 1
    )


def _fallback_delta(mode: str, before: float | None) -> float:
    after = REGISTRY.get_sample_value(
        FALLBACK_METRIC, {"component": COMPONENT, "mode": mode}
    )
    return (after or 0.0) - (before or 0.0)


class FakeVaultClient:
    """Duck-typed stand-in for VaultClient.encode (returns VaultResult)."""

    def __init__(self, fail_values: set[str] | None = None):
        self.calls: list[tuple[str, str | None]] = []
        self.fail_values = fail_values or set()
        self._counter = 0

    async def encode(self, plaintext: str, *, context: str | None = None) -> VaultResult:
        self.calls.append((plaintext, context))
        if plaintext in self.fail_values:
            return VaultResult(ok=False, error="circuit_open")
        self._counter += 1
        return VaultResult(ok=True, value=f"vt{self._counter}", engine="tokenization")


class StubDetector:
    def __init__(self, matches: list[RuleMatch]):
        self._matches = matches

    def detect(self, text: str) -> list[RuleMatch]:
        return list(self._matches)


def _ssn_match(text: str = "123-45-6789") -> RuleMatch:
    return RuleMatch(
        rule_id="pii_ssn_us",
        rule_name="pii_ssn_us",
        value_hash="abc",
        position=(5, 5 + len(text)),
        severity="high",
        action="block",
    )


class TestVaultUnavailableFailClosed:
    def test_reason_constant(self):
        assert VAULT_UNAVAILABLE_REASON == "vault_unavailable_cannot_redact"

    def test_exception_carries_reason_and_context(self):
        exc = VaultUnavailableFailClosed(
            VAULT_UNAVAILABLE_REASON, component=COMPONENT, cause="open"
        )
        assert exc.reason == VAULT_UNAVAILABLE_REASON
        assert str(exc) == VAULT_UNAVAILABLE_REASON
        assert exc.context["cause"] == "open"
        assert exc.context["component"] == "vault"


class TestVaultCircuitBreaker:
    async def test_encode_passes_through_when_closed(self):
        client = _vault_client()
        cb = _vault_breaker(client)

        token = await cb.encode("123-45-6789", context="acme", tenant_id="acme")

        assert token == "vt-abc"
        assert cb.state == "closed"
        client.encode.assert_awaited_once_with("123-45-6789", context="acme")

    async def test_decode_passes_through_when_closed(self):
        client = _vault_client()
        cb = _vault_breaker(client)

        value = await cb.decode("vt-abc", tenant_id="acme")

        assert value == "123-45-6789"
        assert cb.state == "closed"
        client.decode.assert_awaited_once_with("vt-abc", context=None)

    async def test_encode_fail_closed_on_upstream_error(self):
        client = _vault_client(ok=False)
        cb = _vault_breaker(client)
        before = REGISTRY.get_sample_value(
            FALLBACK_METRIC, {"component": COMPONENT, "mode": "fail_closed"}
        )

        with pytest.raises(VaultUnavailableFailClosed) as excinfo:
            await cb.encode("123-45-6789", tenant_id="acme")

        assert excinfo.value.reason == VAULT_UNAVAILABLE_REASON
        assert excinfo.value.context["cause"] == "upstream_error"
        assert excinfo.value.context["error"] == "vault_unreachable"
        assert cb.state == "closed"  # 1 failure < fail_max
        assert _fallback_delta("fail_closed", before) == 1.0

    async def test_decode_fail_closed_on_upstream_error(self):
        client = _vault_client(ok=False)
        cb = _vault_breaker(client)

        with pytest.raises(VaultUnavailableFailClosed) as excinfo:
            await cb.decode("vt-abc")

        assert excinfo.value.reason == VAULT_UNAVAILABLE_REASON
        assert excinfo.value.context["cause"] == "upstream_error"

    async def test_encode_fail_closed_when_open(self):
        client = _vault_client(ok=False)
        cb = _vault_breaker(client, fail_max=5)
        before = REGISTRY.get_sample_value(
            FALLBACK_METRIC, {"component": COMPONENT, "mode": "fail_closed"}
        )

        for _ in range(5):
            with pytest.raises(VaultUnavailableFailClosed):
                await cb.encode("123-45-6789")
        assert cb.state == "open"

        # short-circuit: the upstream is no longer touched
        with pytest.raises(VaultUnavailableFailClosed) as excinfo:
            await cb.encode("123-45-6789")
        assert excinfo.value.context["cause"] == "open"
        assert client.encode.await_count == 5

        assert REGISTRY.get_sample_value(STATE_METRIC, {"component": COMPONENT}) == 1.0
        assert _fallback_delta("fail_closed", before) == 6.0

    async def test_decode_fail_closed_when_open(self):
        client = _vault_client(ok=False)
        cb = _vault_breaker(client, fail_max=2)

        for _ in range(2):
            with pytest.raises(VaultUnavailableFailClosed):
                await cb.decode("vt-abc")
        assert cb.state == "open"

        with pytest.raises(VaultUnavailableFailClosed) as excinfo:
            await cb.decode("vt-abc")
        assert excinfo.value.context["cause"] == "open"
        assert client.decode.await_count == 2

    async def test_fail_closed_when_client_missing(self):
        cb = VaultCircuitBreaker(None, probe_rng=lambda: 0.0)
        with patch(
            "app.core.redaction.vault_client.get_vault_client",
            return_value=None,
        ):
            with pytest.raises(VaultUnavailableFailClosed) as excinfo:
                await cb.encode("123-45-6789")
        assert excinfo.value.context["cause"] == "vault_client_unavailable"
        assert cb.state == "closed"

    async def test_half_open_probe_success_closes(self):
        client = _vault_client(ok=False)
        cb = _vault_breaker(client, fail_max=2)
        for _ in range(2):
            with pytest.raises(VaultUnavailableFailClosed):
                await cb.encode("123-45-6789")
        assert cb.state == "open"

        client.encode.return_value = VaultResult(
            ok=True, value="vt-recovered", engine="tokenization"
        )
        _elapsed_reset_timeout(cb)

        assert await cb.encode("123-45-6789") == "vt-recovered"
        assert cb.state == "closed"

    async def test_half_open_non_probe_fails_closed(self):
        client = _vault_client(ok=False)
        cb = _vault_breaker(client, fail_max=2, probe_rng=lambda: 0.95)
        for _ in range(2):
            with pytest.raises(VaultUnavailableFailClosed):
                await cb.encode("123-45-6789")
        _elapsed_reset_timeout(cb)

        with pytest.raises(VaultUnavailableFailClosed) as excinfo:
            await cb.encode("123-45-6789")
        assert excinfo.value.context["cause"] == "half_open_probe_skipped"
        assert cb.state == "half_open"
        assert client.encode.await_count == 2

    async def test_metrics_listener_exports_state_and_failures(self):
        client = _vault_client(ok=False)
        cb = _vault_breaker(client, fail_max=2)
        assert REGISTRY.get_sample_value(STATE_METRIC, {"component": COMPONENT}) == 0.0

        for _ in range(2):
            with pytest.raises(VaultUnavailableFailClosed):
                await cb.encode("123-45-6789")

        assert REGISTRY.get_sample_value(STATE_METRIC, {"component": COMPONENT}) == 1.0
        assert (
            REGISTRY.get_sample_value(FAILURES_METRIC, {"component": COMPONENT})
            is not None
        )

    def test_singleton_roundtrip(self):
        cb = _vault_breaker()
        set_vault_breaker(cb)
        try:
            assert get_vault_breaker() is cb
        finally:
            set_vault_breaker(None)
        assert get_vault_breaker() is None


class TestRedactorWithVaultBreaker:
    """PIIRedactor integration: breaker-protected encode stays fail-closed."""

    def _redactor(self, vault: FakeVaultClient, breaker: VaultCircuitBreaker | None = None):
        from app.core.redaction.redactor import PIIRedactor

        if breaker is None:
            breaker = VaultCircuitBreaker(vault, probe_rng=lambda: 0.0)
        text = "SSN: 123-45-6789"
        detector = StubDetector([_ssn_match()])
        return PIIRedactor(
            vault_client=vault,  # type: ignore[arg-type]
            detector=detector,
            vault_breaker=breaker,
        ), text

    async def test_mints_tokens_through_breaker(self):
        vault = FakeVaultClient()
        redactor, text = self._redactor(vault)

        result = await redactor.redact(text, tenant_id="acme")

        assert result.ok
        assert result.redacted_text == "SSN: <SSN_T_vt1>"
        assert len(result.tokens) == 1
        assert vault.calls == [("123-45-6789", "acme")]

    async def test_vault_error_fails_closed_with_reason(self):
        vault = FakeVaultClient(fail_values={"123-45-6789"})
        redactor, text = self._redactor(vault)

        result = await redactor.redact(text)

        assert not result.ok
        assert result.error == VAULT_UNAVAILABLE_REASON
        assert result.redacted_text == ""  # fail-closed: no raw text back
        assert result.tokens == []

    async def test_failure_metric_incremented(self):
        from prometheus_client import REGISTRY as _REG

        vault = FakeVaultClient(fail_values={"123-45-6789"})
        redactor, text = self._redactor(vault)
        before = _REG.get_sample_value(
            "scanner_redaction_failures_total", {"error": VAULT_UNAVAILABLE_REASON}
        )

        await redactor.redact(text)

        after = _REG.get_sample_value(
            "scanner_redaction_failures_total", {"error": VAULT_UNAVAILABLE_REASON}
        )
        assert (after or 0.0) == (before or 0.0) + 1.0

    async def test_without_breaker_direct_path_unchanged(self):
        """Explicit client without a breaker keeps the legacy fail-closed path."""
        from app.core.redaction.redactor import PIIRedactor

        vault = FakeVaultClient(fail_values={"123-45-6789"})
        redactor = PIIRedactor(
            vault_client=vault,  # type: ignore[arg-type]
            detector=StubDetector([_ssn_match()]),
        )
        result = await redactor.redact("SSN: 123-45-6789")
        assert not result.ok
        assert result.error == "circuit_open"  # raw client error, as before


class TestRedactMessagesSingletons:
    """redact_messages_for_llm attaches the singleton breaker in prod wiring."""

    async def test_singletons_with_breaker_fail_closed(self):
        from app.core.redaction.redactor import redact_messages_for_llm
        from app.core.redaction.vault_client import set_vault_client
        from app.models.openai import ChatCompletionMessage

        vault = FakeVaultClient(fail_values={"123-45-6789"})
        breaker = VaultCircuitBreaker(vault, probe_rng=lambda: 0.0)
        set_vault_client(vault)  # type: ignore[arg-type]
        set_vault_breaker(breaker)
        try:
            messages = [ChatCompletionMessage(role="user", content="SSN: 123-45-6789")]
            outcome = await redact_messages_for_llm(
                messages, tenant_id="acme", detector=StubDetector([_ssn_match()])
            )
        finally:
            set_vault_client(None)
            set_vault_breaker(None)

        assert not outcome.ok
        assert outcome.error == VAULT_UNAVAILABLE_REASON
        assert messages[0].content == "SSN: 123-45-6789"  # untouched; caller blocks

    async def test_explicit_client_without_breaker_skips_singleton(self):
        """An injected client (tests) must not pick up the singleton breaker."""
        from app.core.redaction.redactor import redact_messages_for_llm
        from app.models.openai import ChatCompletionMessage

        vault = FakeVaultClient()
        set_vault_breaker(VaultCircuitBreaker(FakeVaultClient(), probe_rng=lambda: 0.0))
        try:
            messages = [ChatCompletionMessage(role="user", content="SSN: 123-45-6789")]
            outcome = await redact_messages_for_llm(
                messages,
                vault_client=vault,  # type: ignore[arg-type]
                detector=StubDetector([_ssn_match()]),
            )
        finally:
            set_vault_breaker(None)

        assert outcome.ok
        assert "<SSN_T_vt1>" in messages[0].content
        assert len(vault.calls) == 1
