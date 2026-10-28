"""Integration tests for Vault token TTL / auto-cleanup (EP-13/Prompt 34).

Configuration (same convention as test_vault.py):
``SCANNER_TEST_VAULT_URL``     Base URL of a running Vault; unset => skip.
``SCANNER_TEST_VAULT_TOKEN``   Token (defaults to VAULT_TOKEN, then dev-token).

Typical setup:
    docker compose --profile dev up -d vault
    bash backend/scripts/vault_init.sh
    SCANNER_TEST_VAULT_URL=http://127.0.0.1:8200 pytest tests/integration/test_vault_ttl.py -m vault

The tests rotate the encode key (shared dev-cluster state), so the fixture
restores ``min_decryption_version=1`` afterwards and tokens minted by other
tests keep decoding.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from app.core.redaction.vault_client import VaultClient
from app.core.redaction.vault_ttl import (
    ensure_key_config,
    get_key_state,
    run_cleanup,
)

BACKEND_DIR = Path(__file__).parents[2]
CLEANUP_SCRIPT = BACKEND_DIR / "scripts" / "vault_cleanup.py"

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
        # Hygiene: put the shared key back to the Prompt-34 baseline so the
        # rotations above cannot leak expired-token semantics into other tests.
        try:
            await ensure_key_config_for_restore(client)
        finally:
            await client.close()


async def ensure_key_config_for_restore(client: VaultClient) -> None:
    """Best-effort ``min_decryption_version=1`` restore after the rotations."""
    state = await get_key_state(client)
    if state.supported and state.min_decryption_version > 1:
        if state.engine == "tokenization":
            path = f"/v1/{client.mount}/keys/{client.key_name}"
            body = {
                "allowed_operations": ["encrypt", "decrypt", "verify"],
                "min_decryption_version": 1,
            }
        else:
            path = f"/v1/{client.mount}/keys/{client.key_name}/config"
            body = {"min_decryption_version": 1}
        await client.api("POST", path, json_body=body)


class TestVaultTokenTTL:
    @pytest.mark.asyncio
    async def test_key_config_applied(self, vault_client: VaultClient):
        """Prompt 34 baseline: key reports min_decryption_version + operations."""
        state = await get_key_state(vault_client)
        if state.error == "vault_engine_unavailable":
            pytest.skip("secrets engine not mounted — run backend/scripts/vault_init.sh")
        if not state.supported:
            pytest.skip(f"key exposes no version metadata: {state.error}")

        assert state.latest_version >= 1
        if state.min_decryption_version == 0:
            report = await ensure_key_config(vault_client)
            assert report.ok, report.reason
            state = await get_key_state(vault_client)
        assert state.min_decryption_version >= 1

    @pytest.mark.asyncio
    async def test_token_expires_after_rotation(self, vault_client: VaultClient):
        """Grace window keeps live tokens alive; the next rotation retires them."""
        encoded = await vault_client.encode("123-45-6789", context="vault-ttl-test")
        if encoded.error == "vault_engine_unavailable":
            pytest.skip("secrets engine not mounted — run backend/scripts/vault_init.sh")
        assert encoded.ok, encoded.error

        first = await run_cleanup(vault_client, grace_versions=1)
        if first.skipped and first.reason == "rotation_unsupported":
            pytest.skip("rotate endpoint not available on this Vault build")
        assert first.ok, first.reason

        # Still inside the grace window: the token minted before the rotation
        # must decode — in-flight requests spanning a rotation stay safe.
        alive = await vault_client.decode(encoded.value, context="vault-ttl-test")
        assert alive.ok, alive.error
        assert alive.value == "123-45-6789"

        # Second rotation raises min past the minted version: the token is dead.
        second = await run_cleanup(vault_client, grace_versions=1)
        assert second.ok, second.reason
        dead = await vault_client.decode(encoded.value, context="vault-ttl-test")
        assert not dead.ok, "expired token must no longer decode"

        # The engine itself is healthy: a fresh encode round-trips.
        fresh = await vault_client.encode("123-45-6789", context="vault-ttl-test")
        assert fresh.ok, fresh.error
        assert fresh.value != encoded.value  # new key version => new token
        back = await vault_client.decode(fresh.value, context="vault-ttl-test")
        assert back.ok, back.error
        assert back.value == "123-45-6789"

    @pytest.mark.asyncio
    async def test_cleanup_is_idempotent(self, vault_client: VaultClient):
        first = await run_cleanup(vault_client, grace_versions=1)
        if first.skipped and first.reason == "rotation_unsupported":
            pytest.skip("rotate endpoint not available on this Vault build")
        assert first.ok, first.reason

        second = await run_cleanup(vault_client, grace_versions=1)
        assert second.ok, second.reason
        assert second.to_version is not None
        assert second.min_decryption_version is not None
        # monotonically non-decreasing across passes
        assert second.min_decryption_version >= (first.min_decryption_version or 0)

    @pytest.mark.asyncio
    async def test_dry_run_changes_nothing(self, vault_client: VaultClient):
        encoded = await vault_client.encode("dry-run-probe", context="vault-ttl-test")
        if encoded.error == "vault_engine_unavailable":
            pytest.skip("secrets engine not mounted — run backend/scripts/vault_init.sh")
        assert encoded.ok, encoded.error

        before = await get_key_state(vault_client)
        report = await run_cleanup(vault_client, grace_versions=1, dry_run=True)
        after = await get_key_state(vault_client)

        assert report.ok and report.dry_run
        assert after.latest_version == before.latest_version
        still = await vault_client.decode(encoded.value, context="vault-ttl-test")
        assert still.ok and still.value == "dry-run-probe"

    @pytest.mark.asyncio
    async def test_cleanup_script_once_smoke(self, vault_client: VaultClient):
        """`python scripts/vault_cleanup.py --once` exits 0 against live Vault."""
        if not CLEANUP_SCRIPT.exists():  # pragma: no cover - repo layout guard
            pytest.skip("vault_cleanup.py not found")

        env = {
            **os.environ,
            "SCANNER_VAULT_URL": _vault_url(),
            "SCANNER_VAULT_TOKEN": _vault_token() or "",
        }
        proc = subprocess.run(
            [sys.executable, str(CLEANUP_SCRIPT), "--once"],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
