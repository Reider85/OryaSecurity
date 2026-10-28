"""Integration tests for the Vault client against a live node (EP-13/Prompt 31).

Configuration
-------------
``SCANNER_TEST_VAULT_URL``     Base URL of a running Vault; unset => skip.
``SCANNER_TEST_VAULT_TOKEN``   Token (defaults to VAULT_TOKEN, then dev-token).

Typical setup:
    docker compose --profile dev up -d vault
    bash backend/scripts/vault_init.sh --skip-engine   # or leave engine setup on
    SCANNER_TEST_VAULT_URL=http://127.0.0.1:8200 pytest tests/integration/test_vault.py -m vault
"""

from __future__ import annotations

import os

import httpx
import pytest

from app.core.redaction.vault_client import VaultClient

_MARKERS = [
    pytest.mark.integration,
    pytest.mark.vault,
]


def _vault_url() -> str:
    return os.environ.get("SCANNER_TEST_VAULT_URL", "").rstrip("/")


def _vault_token() -> str | None:
    return (
        os.environ.get("SCANNER_TEST_VAULT_TOKEN")
        or os.environ.get("VAULT_TOKEN")
        or "dev-token"
    )


def _vault_available() -> bool:
    url = _vault_url()
    if not url:
        return False
    try:
        resp = httpx.get(f"{url}/v1/sys/health", timeout=3.0)
    except Exception:
        return False
    # 501 = reachable but not initialized — still "available" for health tests,
    # but encode/decode would fail; require an initialized node.
    return resp.status_code in (200, 429)


pytestmark = _MARKERS + [
    pytest.mark.skipif(
        not _vault_url(),
        reason="SCANNER_TEST_VAULT_URL is not set",
    ),
    pytest.mark.skipif(
        bool(_vault_url()) and not _vault_available(),
        reason="Vault at SCANNER_TEST_VAULT_URL is unreachable or uninitialized",
    ),
]


@pytest.fixture
async def vault_client():
    client = VaultClient(
        vault_url=_vault_url(),
        token=_vault_token(),
        engine="auto",
    )
    try:
        yield client
    finally:
        await client.close()


class TestVaultIntegration:
    @pytest.mark.asyncio
    async def test_health(self, vault_client: VaultClient):
        health = await vault_client.get_health()

        assert health.reachable, health.error
        assert health.initialized
        assert not health.sealed
        assert health.ready, "vault node must be unsealed and active"

    @pytest.mark.asyncio
    async def test_engine_is_resolvable(self, vault_client: VaultClient):
        # Requires backend/scripts/vault_init.sh to have mounted an engine.
        result = await vault_client.encode("probe")
        if result.error == "vault_engine_unavailable":
            pytest.skip(
                "secrets engine not mounted — run backend/scripts/vault_init.sh"
            )
        assert result.ok, result.error
        assert vault_client.resolved_engine in ("tokenization", "transit")

    @pytest.mark.asyncio
    async def test_encode_decode_round_trip(self, vault_client: VaultClient):
        original = "123-45-6789"
        enc = await vault_client.encode(original, context="integration-test")
        if enc.error == "vault_engine_unavailable":
            pytest.skip(
                "secrets engine not mounted — run backend/scripts/vault_init.sh"
            )
        assert enc.ok, enc.error
        assert enc.value
        assert original not in enc.value  # never leaks plaintext

        dec = await vault_client.decode(enc.value, context="integration-test")
        assert dec.ok, dec.error
        assert dec.value == original

    @pytest.mark.asyncio
    async def test_encode_is_deterministic(self, vault_client: VaultClient):
        """Both engines are convergent: same plaintext+context => same token."""
        first = await vault_client.encode("user@example.com", context="tenant-a")
        if first.error == "vault_engine_unavailable":
            pytest.skip("secrets engine not mounted")
        assert first.ok, first.error

        second = await vault_client.encode("user@example.com", context="tenant-a")
        assert second.ok, second.error
        assert second.value == first.value

        # a different context must produce a different transit token
        other = await vault_client.encode("user@example.com", context="tenant-b")
        assert other.ok, other.error
        if vault_client.resolved_engine == "transit":
            assert other.value != first.value

    @pytest.mark.asyncio
    async def test_breaker_is_healthy_after_successes(self, vault_client: VaultClient):
        await vault_client.encode("keep-alive", context="integration-test")
        # a single auth/transport hiccup must not trip the breaker
        assert vault_client.breaker_state in ("closed", "half_open")
