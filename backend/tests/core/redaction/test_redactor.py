"""Unit tests for Vault-based PII tokenization (EP-13/Prompt 32, ADR-0007).

Vault is faked in-memory (or via httpx.MockTransport for the contract test
with the real ``VaultClient``) — no live Vault needed.
"""

from __future__ import annotations

import httpx
import pytest
from prometheus_client import REGISTRY

from app.core.redaction.redactor import (
    PIIRedactor,
    RedactedText,
    RedactionOutcome,
    TokenInfo,
    VAULT_UNAVAILABLE_REASON,
    redact_messages_for_llm,
)
from app.core.redaction.vault_client import VaultClient, VaultResult
from app.core.rules.base import RuleMatch
from app.models.openai import ChatCompletionMessage


def _sample(text: str, rule_id: str = "pii_ssn_us", start: int = 0, end: int | None = None) -> RuleMatch:
    end = len(text) if end is None else end
    return RuleMatch(
        rule_id=rule_id,
        rule_name=rule_id,
        value_hash="abc",
        position=(start, end),
        severity="high",
        action="block",
    )


class FakeVaultClient:
    """Duck-typed stand-in for VaultClient.encode (see redactor call site)."""

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


def _metric(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


class TestRedactPassthrough:
    @pytest.mark.asyncio
    async def test_empty_text(self):
        redactor = PIIRedactor(vault_client=FakeVaultClient())
        result = await redactor.redact("")
        assert result.ok and result.redacted_text == "" and result.tokens == []

    @pytest.mark.asyncio
    async def test_no_matches_returns_unchanged(self):
        redactor = PIIRedactor(vault_client=FakeVaultClient())
        result = await redactor.redact("Hello world")
        assert result.ok
        assert result.redacted_text == "Hello world"
        assert result.tokens == []

    @pytest.mark.asyncio
    async def test_vault_disabled_is_passthrough_even_with_pii(self):
        detector = StubDetector([_sample("SSN: 123-45-6789", start=5, end=16)])
        redactor = PIIRedactor(vault_client=None, detector=detector)
        text = "SSN: 123-45-6789"
        result = await redactor.redact(text)
        assert result.ok
        assert result.redacted_text == text  # unchanged, no tokens minted
        assert result.tokens == []


class TestRedactTokenization:
    @pytest.mark.asyncio
    async def test_ssn_tokenized_via_real_rules(self):
        vault = FakeVaultClient()
        redactor = PIIRedactor(vault_client=vault)
        text = "SSN: 123-45-6789"
        result = await redactor.redact(text)

        assert result.ok
        assert "123-45-6789" not in result.redacted_text
        assert "<SSN_T_vt1>" in result.redacted_text
        assert result.redacted_text == "SSN: <SSN_T_vt1>"

        (token_info,) = result.tokens
        assert token_info.token == "<SSN_T_vt1>"
        assert token_info.type == "SSN"
        assert token_info.position == (5, 16)
        assert len(token_info.original_hash) == 16

    @pytest.mark.asyncio
    async def test_multiple_pii_positions(self):
        vault = FakeVaultClient()
        redactor = PIIRedactor(vault_client=vault)
        text = "SSN: 123-45-6789, email test@example.com"
        result = await redactor.redact(text)

        assert result.ok
        assert "123-45-6789" not in result.redacted_text
        assert "test@example.com" not in result.redacted_text
        assert "<SSN_T_" in result.redacted_text
        assert "<EMAIL_T_" in result.redacted_text
        assert len(result.tokens) == 2
        # Tokens are ordered by their position in the original text
        assert result.tokens[0].type == "SSN"
        assert result.tokens[1].type == "EMAIL"

    @pytest.mark.asyncio
    async def test_secret_tokenized(self):
        vault = FakeVaultClient()
        redactor = PIIRedactor(vault_client=vault)
        text = "Key: AKIAIOSFODNN7EXAMPLE"
        result = await redactor.redact(text)

        assert result.ok
        assert "AKIAIOSFODNN7EXAMPLE" not in result.redacted_text
        assert result.tokens[0].type == "AWS_KEY"
        assert result.tokens[0].token.startswith("<AWS_KEY_T_")

    @pytest.mark.asyncio
    async def test_overlapping_spans_single_replacement(self):
        vault = FakeVaultClient()
        detector = StubDetector(
            [
                _sample("1234567890", start=0, end=5),
                RuleMatch(
                    rule_id="pii_email",
                    rule_name="email",
                    value_hash="def",
                    position=(3, 8),
                    severity="medium",
                    action="block",
                ),
            ]
        )
        redactor = PIIRedactor(vault_client=vault, detector=detector)
        result = await redactor.redact("1234567890")

        assert result.ok
        assert len(result.tokens) == 1
        assert result.redacted_text.count("_T_") == 1

    @pytest.mark.asyncio
    async def test_duplicate_value_single_vault_call_same_token(self):
        vault = FakeVaultClient()
        redactor = PIIRedactor(vault_client=vault)
        text = "SSN 123-45-6789 and again 123-45-6789"
        result = await redactor.redact(text)

        assert result.ok
        assert len(vault.calls) == 1  # deduplicated
        assert len(result.tokens) == 2
        assert result.tokens[0].token == result.tokens[1].token
        assert result.redacted_text.count(result.tokens[0].token) == 2

    @pytest.mark.asyncio
    async def test_tenant_context_passed_to_vault(self):
        vault = FakeVaultClient()
        redactor = PIIRedactor(vault_client=vault)
        await redactor.redact("SSN: 123-45-6789", tenant_id="acme")
        assert vault.calls == [("123-45-6789", "acme")]

    @pytest.mark.asyncio
    async def test_metrics_incremented(self):
        before = _metric("scanner_redaction_tokens_total", type="SSN")
        vault = FakeVaultClient()
        redactor = PIIRedactor(vault_client=vault)
        await redactor.redact("SSN: 123-45-6789")
        after = _metric("scanner_redaction_tokens_total", type="SSN")
        assert after == before + 1


class TestRedactFailClosed:
    @pytest.mark.asyncio
    async def test_encode_failure_returns_empty_text_and_error(self):
        vault = FakeVaultClient(fail_values={"123-45-6789"})
        redactor = PIIRedactor(vault_client=vault)
        result = await redactor.redact("SSN: 123-45-6789")

        assert not result.ok
        assert result.error == "circuit_open"
        # Fail-closed: caller must never see the original text back
        assert result.redacted_text == ""
        assert result.tokens == []

    @pytest.mark.asyncio
    async def test_one_failed_value_discards_partial_results(self):
        # Two PII values; the second encode fails -> no partial redaction leaks.
        vault = FakeVaultClient(fail_values={"test@example.com"})
        detector = StubDetector(
            [
                _sample("123-45-6789", start=5, end=16),
                RuleMatch(
                    rule_id="pii_email",
                    rule_name="email",
                    value_hash="def",
                    position=(24, 40),
                    severity="medium",
                    action="block",
                ),
            ]
        )
        redactor = PIIRedactor(vault_client=vault, detector=detector)
        result = await redactor.redact("SSN: 123-45-6789, email test@example.com")

        assert not result.ok
        assert result.redacted_text == ""
        assert result.tokens == []

    @pytest.mark.asyncio
    async def test_failure_metric_incremented(self):
        before = _metric("scanner_redaction_failures_total", error="circuit_open")
        vault = FakeVaultClient(fail_values={"123-45-6789"})
        redactor = PIIRedactor(vault_client=vault)
        await redactor.redact("SSN: 123-45-6789")
        after = _metric("scanner_redaction_failures_total", error="circuit_open")
        assert after == before + 1


class TestVaultClientContract:
    """The redactor must work against the real VaultClient (mocked transport)."""

    @pytest.mark.asyncio
    async def test_roundtrip_token_is_reversible(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/v1/sys/mounts/"):
                return httpx.Response(200, json={"data": {"type": "tokenization"}})
            if request.url.path.endswith("/encode/default"):
                return httpx.Response(200, json={"data": {"token": "vault-token-42"}})
            if request.url.path.endswith("/decode/default"):
                return httpx.Response(
                    200,
                    json={"data": {"plaintext": "MTIzLTQ1LTY3ODk="}},  # 123-45-6789
                )
            raise AssertionError(f"unexpected path {request.url.path}")

        client = VaultClient(
            vault_url="http://vault.test:8200",
            token="t",
            engine="auto",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        redactor = PIIRedactor(vault_client=client)
        text = "SSN: 123-45-6789"
        result = await redactor.redact(text)

        assert result.ok
        assert result.redacted_text == "SSN: <SSN_T_vault-token-42>"

        # Prompt 33 will extract the inner vault token; prove it decodes back.
        decoded = await client.decode("vault-token-42")
        assert decoded.ok and decoded.value == "123-45-6789"
        await client.close()


class TestRedactMessagesForLlm:
    @pytest.mark.asyncio
    async def test_mutates_content_in_place(self):
        messages = [
            ChatCompletionMessage(role="system", content="You are helpful"),
            ChatCompletionMessage(role="user", content="SSN: 123-45-6789"),
        ]
        outcome = await redact_messages_for_llm(messages, tenant_id="acme", vault_client=FakeVaultClient())

        assert outcome.ok
        assert messages[0].content == "You are helpful"
        assert "123-45-6789" not in messages[1].content
        assert "<SSN_T_" in messages[1].content
        assert len(outcome.tokens) == 1

    @pytest.mark.asyncio
    async def test_skips_empty_content(self):
        messages = [
            ChatCompletionMessage(role="user", content=None),
            ChatCompletionMessage(role="user", content=""),
        ]
        outcome = await redact_messages_for_llm(messages, vault_client=FakeVaultClient())
        assert outcome.ok and outcome.tokens == []

    @pytest.mark.asyncio
    async def test_fail_closed_propagates(self):
        messages = [ChatCompletionMessage(role="user", content="SSN: 123-45-6789")]
        vault = FakeVaultClient(fail_values={"123-45-6789"})
        outcome = await redact_messages_for_llm(messages, vault_client=vault)

        assert not outcome.ok
        assert outcome.error == "circuit_open"
        # Content is left untouched on failure; the caller must block instead.
        assert messages[0].content == "SSN: 123-45-6789"

    @pytest.mark.asyncio
    async def test_vault_disabled_passthrough(self):
        messages = [ChatCompletionMessage(role="user", content="SSN: 123-45-6789")]
        outcome = await redact_messages_for_llm(messages, vault_client=None)
        assert outcome.ok and outcome.tokens == []
        assert messages[0].content == "SSN: 123-45-6789"


class TestHelpers:
    def test_vault_unavailable_reason(self):
        assert VAULT_UNAVAILABLE_REASON == "vault_unavailable_cannot_redact"

    def test_redacted_text_ok_property(self):
        assert RedactedText(redacted_text="x").ok
        assert not RedactedText(redacted_text="", error="boom").ok

    def test_redaction_outcome_ok_property(self):
        assert RedactionOutcome().ok
        assert not RedactionOutcome(error="boom").ok

    def test_token_info_is_frozen(self):
        info = TokenInfo(token="<SSN_T_1>", type="SSN", original_hash="h", position=(0, 1))
        with pytest.raises(Exception):
            info.token = "<X>"  # type: ignore[misc]
