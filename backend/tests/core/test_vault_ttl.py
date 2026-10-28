"""Unit tests for Vault token TTL / auto-cleanup (EP-13/Prompt 34): key
rotation, ``min_decryption_version`` bookkeeping, the cleanup CLI. Transport is
mocked via httpx.MockTransport — no live Vault needed (live coverage lives in
tests/integration/test_vault_ttl.py).
"""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

from app.config import settings
from app.core.redaction.vault_client import VaultClient
from app.core.redaction.vault_ttl import (
    CleanupReport,
    ensure_key_config,
    get_key_state,
    probe_roundtrip,
    run_cleanup,
)

SCRIPT = Path(__file__).parents[2] / "scripts" / "vault_cleanup.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("vault_cleanup", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cleanup_script = _load_script()


class FakeVault:
    """Minimal stateful Vault (transit or tokenization) for TTL tests."""

    def __init__(
        self,
        *,
        engine_type: str = "transit",
        latest_version: int = 1,
        min_decryption_version: int = 1,
        rotate_status: int = 204,
        config_status: int = 204,
        key_status: int = 200,
        expose_versions: bool = False,
    ):
        self.engine_type = engine_type
        self.latest_version = latest_version
        self.min_decryption_version = min_decryption_version
        self.rotate_status = rotate_status
        self.config_status = config_status
        self.key_status = key_status
        self.expose_versions = expose_versions
        self.rotate_calls = 0
        self.config_calls = 0
        self.config_bodies: list[dict] = []
        self.writes: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        body = json.loads(request.content) if request.content else None

        if path.startswith("/v1/sys/mounts/"):
            return httpx.Response(200, json={"data": {"type": self.engine_type}})

        if path == "/v1/tokenization/keys/default":
            if method == "GET":
                if self.key_status != 200:
                    return httpx.Response(self.key_status, json={"errors": ["no such key"]})
                data: dict = {"min_decryption_version": self.min_decryption_version}
                if self.expose_versions:
                    data["versions"] = {str(v): {} for v in range(1, self.latest_version + 1)}
                else:
                    data["latest_version"] = self.latest_version
                return httpx.Response(200, json={"data": data})
            # tokenization folds key config into the key itself
            assert self.engine_type == "tokenization", (
                f"transit must not POST key config at {path}"
            )
            assert body is not None
            self.config_calls += 1
            self.config_bodies.append(body)
            self.writes.append((method, path))
            if self.config_status >= 400:
                return httpx.Response(self.config_status, json={"errors": ["nope"]})
            self.min_decryption_version = int(
                body.get("min_decryption_version", self.min_decryption_version)
            )
            return httpx.Response(self.config_status)

        if path == "/v1/tokenization/keys/default/rotate":
            self.rotate_calls += 1
            self.writes.append((method, path))
            if self.rotate_status >= 400:
                return httpx.Response(self.rotate_status, json={"errors": ["denied"]})
            self.latest_version += 1
            return httpx.Response(self.rotate_status)

        if path == "/v1/tokenization/keys/default/config":
            assert self.engine_type == "transit", (
                f"tokenization must not POST transit config at {path}"
            )
            assert body is not None
            self.config_calls += 1
            self.config_bodies.append(body)
            self.writes.append((method, path))
            if self.config_status >= 400:
                return httpx.Response(self.config_status, json={"errors": ["nope"]})
            self.min_decryption_version = int(
                body.get("min_decryption_version", self.min_decryption_version)
            )
            return httpx.Response(self.config_status)

        if path == "/v1/tokenization/encrypt/default":
            assert body is not None
            plaintext = body["plaintext"]
            return httpx.Response(
                200,
                json={"data": {"ciphertext": f"vault:v{self.latest_version}:{plaintext}"}},
            )

        if path == "/v1/tokenization/decrypt/default":
            assert body is not None
            parts = body["ciphertext"].split(":", 2)
            if len(parts) != 3:
                return httpx.Response(400, json={"errors": ["bad ciphertext"]})
            plaintext_b64 = parts[2]
            version = int(parts[1][1:])
            if version < self.min_decryption_version:
                return httpx.Response(400, json={"errors": ["version deprecated"]})
            return httpx.Response(200, json={"data": {"plaintext": plaintext_b64}})

        raise AssertionError(f"unexpected request: {method} {path}")


def _client(fake: FakeVault, **kwargs) -> VaultClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return VaultClient(
        vault_url="http://vault.test:8200",
        token="test-root-token",
        client=http,
        **kwargs,
    )


class TestGetKeyState:
    @pytest.mark.asyncio
    async def test_reads_versions_for_transit(self):
        fake = FakeVault(latest_version=3, min_decryption_version=2)
        state = await get_key_state(_client(fake, engine="auto"))

        assert state.supported
        assert state.engine == "transit"
        assert state.latest_version == 3
        assert state.min_decryption_version == 2

    @pytest.mark.asyncio
    async def test_versions_map_fallback(self):
        fake = FakeVault(latest_version=4, min_decryption_version=1, expose_versions=True)
        state = await get_key_state(_client(fake, engine="auto"))

        assert state.supported
        assert state.latest_version == 4

    @pytest.mark.asyncio
    async def test_engine_unavailable(self):
        fake = FakeVault(engine_type="kv-v2")
        state = await get_key_state(_client(fake, engine="auto"))

        assert not state.supported
        assert state.error == "vault_engine_unavailable"

    @pytest.mark.asyncio
    async def test_missing_key_is_an_error(self):
        fake = FakeVault(key_status=404)
        state = await get_key_state(_client(fake, engine="auto"))

        assert not state.supported
        assert state.error and state.error.startswith("vault_key_read_failed")

    @pytest.mark.asyncio
    async def test_unsupported_when_no_version_fields(self):
        fake = FakeVault()
        fake.expose_versions = True
        fake.latest_version = 0  # versions map empty -> latest stays None
        state = await get_key_state(_client(fake, engine="auto"))

        assert not state.supported
        assert state.error == "vault_key_state_unsupported"

    @pytest.mark.asyncio
    async def test_transport_error_is_unreachable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = VaultClient(
            vault_url="http://vault.test:8200", token="t", client=http, engine="transit"
        )
        state = await get_key_state(client)

        assert state.error == "vault_unreachable"
        await client.close()


class TestEnsureKeyConfig:
    @pytest.mark.asyncio
    async def test_noop_when_already_configured(self):
        fake = FakeVault(min_decryption_version=1)
        report = await ensure_key_config(_client(fake, engine="auto"))

        assert report.ok
        assert report.reason == "already_configured"
        assert fake.config_calls == 0

    @pytest.mark.asyncio
    async def test_writes_baseline_transit_config(self):
        fake = FakeVault(min_decryption_version=0)
        report = await ensure_key_config(_client(fake, engine="auto"))

        assert report.ok
        assert fake.config_calls == 1
        assert fake.config_bodies[0] == {"min_decryption_version": 1}
        assert fake.min_decryption_version == 1

    @pytest.mark.asyncio
    async def test_tokenization_sends_allowed_operations(self):
        fake = FakeVault(engine_type="tokenization", min_decryption_version=0)
        report = await ensure_key_config(_client(fake, engine="auto"))

        assert report.ok
        assert fake.config_bodies[0] == {
            "allowed_operations": ["encrypt", "decrypt", "verify"],
            "min_decryption_version": 1,
        }

    @pytest.mark.asyncio
    async def test_never_lowers_existing_min_version(self):
        fake = FakeVault(latest_version=5, min_decryption_version=4)
        report = await ensure_key_config(_client(fake, engine="auto"))

        assert report.ok
        assert report.min_decryption_version == 4
        assert fake.config_calls == 0  # min stays 4 — expired tokens stay dead

    @pytest.mark.asyncio
    async def test_key_already_exists_is_ok(self):
        fake = FakeVault(min_decryption_version=0, config_status=400)
        report = await ensure_key_config(_client(fake, engine="auto"))

        assert report.ok
        assert report.reason == "already_exists"

    @pytest.mark.asyncio
    async def test_write_failure_is_an_error(self):
        fake = FakeVault(min_decryption_version=0, config_status=500)
        report = await ensure_key_config(_client(fake, engine="auto"))

        assert not report.ok
        assert not report.skipped
        assert report.reason and report.reason.startswith("vault_key_config_failed")

    @pytest.mark.asyncio
    async def test_dry_run_sends_no_writes(self):
        fake = FakeVault(min_decryption_version=0)
        report = await ensure_key_config(_client(fake, engine="auto"), dry_run=True)

        assert report.ok and report.dry_run
        assert fake.writes == []


class TestRunCleanup:
    @pytest.mark.asyncio
    async def test_rotates_and_keeps_grace_version(self):
        fake = FakeVault(latest_version=1, min_decryption_version=1)
        report = await run_cleanup(_client(fake, engine="auto"), grace_versions=1)

        assert report.ok and report.rotated
        assert report.status == "ok"
        assert report.from_version == 1
        assert report.to_version == 2
        assert report.min_decryption_version == 1  # max(1, 2-1)
        assert fake.latest_version == 2
        assert fake.min_decryption_version == 1

    @pytest.mark.asyncio
    async def test_grace_zero_retires_all_older_versions(self):
        fake = FakeVault(latest_version=1, min_decryption_version=1)
        report = await run_cleanup(_client(fake, engine="auto"), grace_versions=0)

        assert report.ok
        assert report.min_decryption_version == 2  # max(1, 2-0)
        assert fake.min_decryption_version == 2

    @pytest.mark.asyncio
    async def test_min_version_is_monotonic(self):
        fake = FakeVault(latest_version=2, min_decryption_version=4)
        report = await run_cleanup(_client(fake, engine="auto"), grace_versions=1)

        assert report.ok
        # target = max(4, 3-1) = 4 — an out-of-order job cannot lower min
        assert report.min_decryption_version == 4
        assert fake.min_decryption_version == 4

    @pytest.mark.asyncio
    async def test_multiple_passes_expire_previous_window(self):
        fake = FakeVault(latest_version=1, min_decryption_version=1)
        client = _client(fake, engine="auto")

        first = await run_cleanup(client, grace_versions=1)
        assert first.ok and first.min_decryption_version == 1
        # a token minted under v1 is still decode-able during the grace window
        assert (await probe_roundtrip(client)) is None

        second = await run_cleanup(client, grace_versions=1)
        assert second.ok and second.min_decryption_version == 2
        await client.close()

    @pytest.mark.asyncio
    async def test_rotate_unsupported_is_skipped(self):
        fake = FakeVault(rotate_status=404)
        report = await run_cleanup(_client(fake, engine="auto"))

        assert not report.ok
        assert report.skipped
        assert report.status == "skipped"
        assert report.reason == "rotation_unsupported"

    @pytest.mark.asyncio
    async def test_rotate_forbidden_is_an_error(self):
        fake = FakeVault(rotate_status=403)
        report = await run_cleanup(_client(fake, engine="auto"))

        assert not report.ok and not report.skipped
        assert report.status == "error"
        assert report.reason == "rotation_forbidden"

    @pytest.mark.asyncio
    async def test_transport_error_is_an_error(self):
        fake = FakeVault(rotate_status=500)
        report = await run_cleanup(_client(fake, engine="auto"))

        assert report.status == "error"
        assert report.reason and "vault_rotate_failed" in report.reason

    @pytest.mark.asyncio
    async def test_unsupported_engine_is_skipped(self):
        fake = FakeVault(expose_versions=True)
        fake.latest_version = 0
        report = await run_cleanup(_client(fake, engine="auto"))

        assert report.skipped
        assert report.reason == "vault_key_state_unsupported"

    @pytest.mark.asyncio
    async def test_dry_run_rotates_nothing(self):
        fake = FakeVault(latest_version=1, min_decryption_version=1)
        report = await run_cleanup(_client(fake, engine="auto"), dry_run=True)

        assert report.ok and report.dry_run
        assert fake.writes == []
        assert fake.latest_version == 1
        assert report.to_version == 2  # what would have happened

    @pytest.mark.asyncio
    async def test_config_failure_after_rotate_reports_error(self):
        # Rotate succeeds (204) but the follow-up config write fails.
        fake = FakeVault(latest_version=1, min_decryption_version=1)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/config") and request.method == "POST":
                return httpx.Response(500, json={"errors": ["boom"]})
            return fake.handler(request)

        client = VaultClient(
            vault_url="http://vault.test:8200",
            token="t",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            engine="auto",
        )
        report = await run_cleanup(client, grace_versions=1)

        assert not report.ok
        assert report.rotated  # rotation itself happened
        assert report.reason and "vault_min_version_update_failed" in report.reason
        await client.close()

    @pytest.mark.asyncio
    async def test_ensure_false_skips_baseline_write(self):
        fake = FakeVault(min_decryption_version=0)
        report = await run_cleanup(_client(fake, engine="auto"), ensure=False)

        assert report.ok
        # only the rotate + min-bump write, no separate baseline ensure write
        assert fake.config_calls == 1
        assert fake.config_bodies[0] == {"min_decryption_version": 1}

    @pytest.mark.asyncio
    async def test_tokenization_rotation_path(self):
        fake = FakeVault(engine_type="tokenization", latest_version=1, min_decryption_version=1)
        report = await run_cleanup(_client(fake, engine="auto"), grace_versions=1)

        assert report.ok and report.engine == "tokenization"
        assert fake.config_bodies[-1] == {
            "allowed_operations": ["encrypt", "decrypt", "verify"],
            "min_decryption_version": 1,
        }

    def test_report_status_property(self):
        assert CleanupReport(ok=True).status == "ok"
        assert CleanupReport(skipped=True).status == "skipped"
        assert CleanupReport().status == "error"


class TestProbeRoundtrip:
    @pytest.mark.asyncio
    async def test_success(self):
        fake = FakeVault()
        assert await probe_roundtrip(_client(fake, engine="auto")) is None

    @pytest.mark.asyncio
    async def test_encode_failure(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"errors": ["down"]})

        client = VaultClient(
            vault_url="http://vault.test:8200",
            token="t",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            engine="transit",
        )
        error = await probe_roundtrip(client)
        assert error and "vault_encode_failed" in error
        await client.close()

    @pytest.mark.asyncio
    async def test_expired_version_fails_decode(self):
        # min_decryption_version already retired the probe's key version.
        fake = FakeVault(latest_version=1, min_decryption_version=5)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/tokenization/encrypt/default":
                body = json.loads(request.content)
                return httpx.Response(
                    200,
                    json={"data": {"ciphertext": f"vault:v1:{body['plaintext']}"}},
                )
            return fake.handler(request)

        client = VaultClient(
            vault_url="http://vault.test:8200",
            token="t",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            engine="transit",
        )
        error = await probe_roundtrip(client)
        assert error and "vault_decode_failed" in error
        await client.close()


class TestCleanupScriptCli:
    def test_defaults(self):
        args = cleanup_script.build_parser().parse_args([])

        assert args.once is False and args.loop is False
        assert args.interval == settings.vault_token_ttl_seconds
        assert args.grace_versions == settings.vault_token_ttl_grace_versions
        assert args.ensure_config is True
        assert args.verify is True
        assert args.dry_run is False

    def test_flags(self):
        args = cleanup_script.build_parser().parse_args(
            [
                "--loop",
                "--interval",
                "60",
                "--grace-versions",
                "0",
                "--dry-run",
                "--no-verify",
                "--no-ensure-config",
                "--vault-url",
                "http://v:8200",
                "--mount",
                "pii",
                "--key",
                "k1",
            ]
        )

        assert args.loop is True
        assert args.interval == 60
        assert args.grace_versions == 0
        assert args.dry_run is True
        assert args.verify is False
        assert args.ensure_config is False
        assert args.vault_url == "http://v:8200"
        assert args.mount == "pii"
        assert args.key == "k1"

    def test_main_without_vault_url_exits_2(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "vault_url", "")
        assert cleanup_script.main([]) == 2

    @pytest.mark.asyncio
    async def test_run_once_ok(self):
        fake = FakeVault(latest_version=1, min_decryption_version=1)
        code = await cleanup_script.run_once(
            _client(fake, engine="auto"), grace_versions=1
        )

        assert code == 0
        assert fake.rotate_calls == 1

    @pytest.mark.asyncio
    async def test_run_once_skip_is_zero(self):
        fake = FakeVault(rotate_status=404)
        assert await cleanup_script.run_once(_client(fake, engine="auto")) == 0

    @pytest.mark.asyncio
    async def test_run_once_error_is_one(self):
        fake = FakeVault(rotate_status=403)
        assert await cleanup_script.run_once(_client(fake, engine="auto")) == 1

    @pytest.mark.asyncio
    async def test_run_once_dry_run_ok_without_writes(self):
        fake = FakeVault(latest_version=1, min_decryption_version=1)
        code = await cleanup_script.run_once(
            _client(fake, engine="auto"), dry_run=True
        )

        assert code == 0
        assert fake.writes == []

    @pytest.mark.asyncio
    async def test_run_once_probe_failure_still_ok(self):
        # Rotation succeeded but decode of the probe fails: log-only WARN.
        fake = FakeVault(latest_version=1, min_decryption_version=1)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/tokenization/decrypt/default":
                return httpx.Response(400, json={"errors": ["version deprecated"]})
            return fake.handler(request)

        client = VaultClient(
            vault_url="http://vault.test:8200",
            token="t",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            engine="auto",
        )
        assert await cleanup_script.run_once(client, grace_versions=0) == 0
        await client.close()


class TestKeyStateTransitFallbackPaths:
    """Sanity: the fake mirrors the real path layout used by VaultClient."""

    @pytest.mark.asyncio
    async def test_transit_encrypt_uses_convergent_context(self):
        fake = FakeVault()
        client = _client(fake, engine="auto")
        result = await client.encode("123-45-6789", context="tenant")

        assert result.ok
        assert result.value.startswith("vault:v1:")
        assert base64.b64decode(result.value.split(":", 2)[2]) == b"123-45-6789"
        await client.close()
