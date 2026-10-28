"""Unit tests for reverse tokenization (EP-13/Prompt 33, ADR-0007).

Vault is faked in-memory (or via httpx.MockTransport for the contract test
with the real ``VaultClient``) — no live Vault needed.
"""

from __future__ import annotations

import httpx
import pytest
from prometheus_client import REGISTRY

from app.core.redaction.detokenizer import (
    PIIDetokenizer,
    StreamingDetokenizer,
    detokenize_text,
    find_tokens,
)
from app.core.redaction.redactor import PIIRedactor
from app.core.redaction.vault_client import VaultClient, VaultResult, set_vault_client


class FakeVaultClient:
    """Duck-typed stand-in for VaultClient.decode (see detokenizer call site)."""

    def __init__(self, fail_tokens: set[str] | None = None):
        self.calls: list[tuple[str, str | None]] = []
        self.fail_tokens = fail_tokens or set()
        # token -> original value registry
        self.registry: dict[str, str] = {}

    async def decode(self, token: str, *, context: str | None = None) -> VaultResult:
        self.calls.append((token, context))
        if token in self.fail_tokens:
            return VaultResult(ok=False, error="circuit_open")
        value = self.registry.get(token)
        if value is None:
            return VaultResult(ok=False, error="vault_decode_failed: unknown token")
        return VaultResult(ok=True, value=value, engine="tokenization")


def _vault_with(mapping: dict[str, str]) -> FakeVaultClient:
    vault = FakeVaultClient()
    vault.registry = dict(mapping)
    return vault


def _metric(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


class TestFindTokens:
    def test_finds_single_token(self):
        found = find_tokens("SSN: <SSN_T_vt1>")
        assert found == [("<SSN_T_vt1>", "SSN", "vt1")]

    def test_finds_multiple_tokens(self):
        found = find_tokens("<SSN_T_a> and <EMAIL_T_b>")
        assert [(t, i) for _, t, i in found] == [("SSN", "a"), ("EMAIL", "b")]

    def test_no_tokens(self):
        assert find_tokens("plain text with <html> markup") == []

    def test_ignores_legacy_hash_placeholder(self):
        # Irreversible audit placeholder must never be decoded.
        assert find_tokens("<SSN_HASH_deadbeefdeadbeef>") == []

    def test_ignores_redacted_marker(self):
        assert find_tokens("value is <REDACTED> now") == []

    def test_type_with_underscores(self):
        found = find_tokens("<AWS_KEY_T_vt9>")
        assert found == [("<AWS_KEY_T_vt9>", "AWS_KEY", "vt9")]

    def test_non_greedy_split_when_value_contains_separator(self):
        # Vault token containing "_T_" must still split on the FIRST separator.
        found = find_tokens("<SSN_T_abc_T_def>")
        assert found == [("<SSN_T_abc_T_def>", "SSN", "abc_T_def")]


class TestDetokenize:
    @pytest.mark.asyncio
    async def test_empty_text(self):
        detok = PIIDetokenizer(vault_client=_vault_with({}))
        assert await detok.detokenize("") == ""

    @pytest.mark.asyncio
    async def test_no_tokens_returns_unchanged_without_vault_calls(self):
        vault = _vault_with({})
        detok = PIIDetokenizer(vault_client=vault)
        assert await detok.detokenize("Hello world") == "Hello world"
        assert vault.calls == []

    @pytest.mark.asyncio
    async def test_vault_disabled_is_passthrough(self):
        # No client injected and no process singleton -> leave text as-is.
        detok = PIIDetokenizer(vault_client=None)
        set_vault_client(None)
        try:
            text = "SSN: <SSN_T_vt1>"
            assert await detok.detokenize(text) == text
        finally:
            set_vault_client(None)

    @pytest.mark.asyncio
    async def test_single_token_restored(self):
        vault = _vault_with({"vt1": "123-45-6789"})
        detok = PIIDetokenizer(vault_client=vault)
        out = await detok.detokenize("Your SSN <SSN_T_vt1> is valid")
        assert out == "Your SSN 123-45-6789 is valid"
        assert vault.calls == [("vt1", None)]

    @pytest.mark.asyncio
    async def test_multiple_unique_tokens_concurrent(self):
        vault = _vault_with({"vt1": "123-45-6789", "vt2": "a@b.com"})
        detok = PIIDetokenizer(vault_client=vault)
        out = await detok.detokenize("<SSN_T_vt1> / <EMAIL_T_vt2>")
        assert out == "123-45-6789 / a@b.com"
        # One round-trip per *distinct* vault token.
        assert sorted(t for t, _ in vault.calls) == ["vt1", "vt2"]

    @pytest.mark.asyncio
    async def test_duplicate_token_decoded_once(self):
        vault = _vault_with({"vt1": "123-45-6789"})
        detok = PIIDetokenizer(vault_client=vault)
        out = await detok.detokenize("<SSN_T_vt1> and again <SSN_T_vt1>")
        assert out == "123-45-6789 and again 123-45-6789"
        assert len(vault.calls) == 1

    @pytest.mark.asyncio
    async def test_lookup_failure_keeps_token_in_place(self):
        # Fail-open (ADR-0008 inbound): never raise, never drop the token.
        vault = _vault_with({})
        vault.fail_tokens.add("vt9")
        detok = PIIDetokenizer(vault_client=vault)
        text = "SSN: <SSN_T_vt9>"
        assert await detok.detokenize(text) == text

    @pytest.mark.asyncio
    async def test_partial_failure_keeps_only_failed_token(self):
        vault = _vault_with({"vt1": "123-45-6789"})
        vault.fail_tokens.add("vt2")
        detok = PIIDetokenizer(vault_client=vault)
        out = await detok.detokenize("<SSN_T_vt1> / <EMAIL_T_vt2>")
        assert out == "123-45-6789 / <EMAIL_T_vt2>"

    @pytest.mark.asyncio
    async def test_tenant_context_passed_to_decode(self):
        vault = _vault_with({"vt1": "123-45-6789"})
        detok = PIIDetokenizer(vault_client=vault)
        await detok.detokenize("<SSN_T_vt1>", tenant_id="acme")
        assert vault.calls == [("vt1", "acme")]

    @pytest.mark.asyncio
    async def test_metrics_incremented(self):
        before_ok = _metric(
            "scanner_detokenization_tokens_total", type="SSN"
        )
        before_err = _metric(
            "scanner_detokenization_failures_total", error="circuit_open"
        )

        vault = _vault_with({"vt1": "123-45-6789"})
        vault.fail_tokens.add("vt2")
        detok = PIIDetokenizer(vault_client=vault)
        await detokenize_text(
            "<SSN_T_vt1> <EMAIL_T_vt2>", vault_client=vault
        )

        after_ok = _metric("scanner_detokenization_tokens_total", type="SSN")
        after_err = _metric(
            "scanner_detokenization_failures_total", error="circuit_open"
        )
        assert after_ok == before_ok + 1
        assert after_err == before_err + 1

    @pytest.mark.asyncio
    async def test_detokenize_text_uses_singleton(self):
        vault = _vault_with({"vt1": "123-45-6789"})
        set_vault_client(vault)  # type: ignore[arg-type]
        try:
            out = await detokenize_text("SSN: <SSN_T_vt1>")
            assert out == "SSN: 123-45-6789"
        finally:
            set_vault_client(None)


class TestRedactDetokenizeRoundtrip:
    """Prompt 32 mints the tokens Prompt 33 restores — the full loop must
    produce the original text back."""

    @pytest.mark.asyncio
    async def test_roundtrip_with_real_vault_client(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/v1/sys/mounts/"):
                return httpx.Response(200, json={"data": {"type": "tokenization"}})
            if request.url.path.endswith("/encode/default"):
                return httpx.Response(200, json={"data": {"token": "vault-token-42"}})
            if request.url.path.endswith("/decode/default"):
                # Plaintext "123-45-6789"
                return httpx.Response(
                    200, json={"data": {"plaintext": "MTIzLTQ1LTY3ODk="}}
                )
            raise AssertionError(f"unexpected path {request.url.path}")

        client = VaultClient(
            vault_url="http://vault.test:8200",
            token="t",
            engine="auto",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        try:
            redactor = PIIRedactor(vault_client=client)
            detokenizer = PIIDetokenizer(vault_client=client)

            original = "SSN: 123-45-6789"
            redacted = await redactor.redact(original)
            assert redacted.ok
            assert redacted.redacted_text == "SSN: <SSN_T_vault-token-42>"

            restored = await detokenizer.detokenize(redacted.redacted_text)
            assert restored == original
        finally:
            await client.close()


class TestStreamingDetokenizer:
    @pytest.mark.asyncio
    async def test_plain_text_passes_through_immediately(self):
        detok = StreamingDetokenizer(vault_client=_vault_with({}))
        assert await detok.feed("Hello world") == "Hello world"
        assert await detok.flush() == ""

    @pytest.mark.asyncio
    async def test_complete_token_in_one_feed(self):
        detok = StreamingDetokenizer(
            vault_client=_vault_with({"vt1": "123-45-6789"})
        )
        out = await detok.feed("SSN <SSN_T_vt1> ok")
        assert out == "SSN 123-45-6789 ok"

    @pytest.mark.asyncio
    async def test_token_split_across_feeds(self):
        detok = StreamingDetokenizer(
            vault_client=_vault_with({"vault-token-42": "123-45-6789"})
        )
        first = await detok.feed("Your SSN <SSN_T_vau")
        assert first == "Your SSN "  # tail held until the token closes
        second = await detok.feed("lt-token-42> is valid")
        assert second == "123-45-6789 is valid"
        assert await detok.flush() == ""

    @pytest.mark.asyncio
    async def test_token_split_into_three_feeds(self):
        detok = StreamingDetokenizer(
            vault_client=_vault_with({"vt1": "restored"})
        )
        assert await detok.feed("<SS") == ""
        assert await detok.feed("N_T_") == ""
        # The closing ">" is part of the token, so it is consumed with it.
        assert await detok.feed("vt1>x") == "restoredx"

    @pytest.mark.asyncio
    async def test_html_like_text_not_held(self):
        # "<div" / "<3" cannot become a token (type must be [A-Z0-9_]+) —
        # they must not stall the stream.
        detok = StreamingDetokenizer(vault_client=_vault_with({}))
        assert await detok.feed("a <div class") == "a <div class"
        assert await detok.feed(">x") == ">x"
        assert await detok.feed(" 3 <3") == " 3 <3"

    @pytest.mark.asyncio
    async def test_uppercase_markup_held_then_released(self):
        detok = StreamingDetokenizer(vault_client=_vault_with({}))
        # "<SSN" looks like a token type prefix; when it turns out not to be
        # a token, the buffer is released verbatim on flush.
        assert await detok.feed("x <SSN") == "x "
        assert await detok.flush() == "<SSN"

    @pytest.mark.asyncio
    async def test_max_hold_cap_treated_as_literal(self):
        detok = StreamingDetokenizer(vault_client=_vault_with({}), max_hold=16)
        long_partial = "y" + "<" + "A" * 40
        out = await detok.feed(long_partial)
        # Too long to plausibly be a token — emitted as literal text.
        assert out == long_partial
        assert await detok.flush() == ""

    @pytest.mark.asyncio
    async def test_flush_releases_unclosed_token_verbatim(self):
        detok = StreamingDetokenizer(vault_client=_vault_with({}))
        assert await detok.feed("tail <SSN_T_vt") == "tail "
        assert await detok.flush() == "<SSN_T_vt"

    @pytest.mark.asyncio
    async def test_discard_drops_buffer(self):
        detok = StreamingDetokenizer(vault_client=_vault_with({}))
        await detok.feed("hold <SSN_T_vt")
        detok.discard()
        assert await detok.flush() == ""

    @pytest.mark.asyncio
    async def test_feed_empty_delta(self):
        detok = StreamingDetokenizer(vault_client=_vault_with({}))
        assert await detok.feed("") == ""

    @pytest.mark.asyncio
    async def test_tenant_context_propagated(self):
        vault = _vault_with({"vt1": "restored"})
        detok = StreamingDetokenizer(vault_client=vault, tenant_id="acme")
        await detok.feed("<SSN_T_vt1>")
        assert vault.calls == [("vt1", "acme")]

    @pytest.mark.asyncio
    async def test_failure_keeps_partial_token(self):
        vault = _vault_with({})
        vault.fail_tokens.add("vt1")
        detok = StreamingDetokenizer(vault_client=vault)
        out = await detok.feed("x <SSN_T_vt1> y")
        assert out == "x <SSN_T_vt1> y"
