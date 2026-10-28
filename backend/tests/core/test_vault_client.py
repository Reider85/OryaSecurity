"""Unit tests for the Vault client (EP-13/Prompt 31): health probing,
tokenization/transit encode+decode, engine auto-detection, circuit breaker,
singleton lifecycle. Transport is mocked via httpx.MockTransport — no live
Vault needed (live coverage lives in tests/integration/test_vault.py)."""

from __future__ import annotations

import base64
import json
import time

import httpx
import pytest

from app.config import settings
from app.core.redaction.vault_client import (
    VaultClient,
    VaultResult,
    close_vault_client,
    get_vault_client,
    init_vault_client,
    set_vault_client,
)


def _client(handler, **kwargs) -> VaultClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return VaultClient(
        vault_url=kwargs.pop("vault_url", "http://vault.test:8200"),
        token=kwargs.pop("token", "test-root-token"),
        client=http,
        **kwargs,
    )


class TestHealth:
    @pytest.mark.asyncio
    async def test_active_unsealed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/sys/health"
            return httpx.Response(
                200,
                json={"type": "raft", "initialized": True, "sealed": False, "standby": False},
            )

        client = _client(handler, engine="tokenization")
        health = await client.get_health()

        assert health.reachable and health.initialized
        assert not health.sealed and health.active and health.ready
        await client.close()

    @pytest.mark.asyncio
    async def test_standby_node(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429, json={"initialized": True, "sealed": False, "standby": True}
            )

        client = _client(handler, engine="tokenization")
        health = await client.get_health()

        assert health.reachable and health.initialized and not health.sealed
        assert health.standby and not health.active
        assert not health.ready  # standby is not the active node
        await client.close()

    @pytest.mark.asyncio
    async def test_not_initialized(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(501, json={"initialized": False, "sealed": True})

        client = _client(handler, engine="tokenization")
        health = await client.get_health()

        assert health.reachable
        assert not health.initialized and health.sealed and not health.ready
        await client.close()

    @pytest.mark.asyncio
    async def test_sealed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"initialized": True, "sealed": True})

        client = _client(handler, engine="tokenization")
        health = await client.get_health()

        assert health.reachable and health.initialized and health.sealed
        assert not health.ready
        await client.close()

    @pytest.mark.asyncio
    async def test_unreachable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        client = _client(handler, engine="tokenization")
        health = await client.get_health()

        assert not health.reachable
        assert health.error == "vault_unreachable"
        await client.close()


class TestEngineResolution:
    @pytest.mark.asyncio
    async def test_auto_detects_tokenization(self):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path.startswith("/v1/sys/mounts/"):
                return httpx.Response(200, json={"data": {"type": "tokenization"}})
            assert request.url.path == "/v1/tokenization/encode/default"
            return httpx.Response(200, json={"data": {"token": "tok-1"}})

        client = _client(handler, engine="auto")
        result = await client.encode("123-45-6789")

        assert result.ok and result.value == "tok-1"
        assert result.engine == "tokenization"
        assert client.resolved_engine == "tokenization"
        # second call uses the cached resolution (no re-probe)
        assert (await client.encode("123-45-6789")).ok
        assert [p for p in calls if p.startswith("/v1/sys/mounts/")] == [
            "/v1/sys/mounts/tokenization"
        ]
        await client.close()

    @pytest.mark.asyncio
    async def test_auto_detects_transit_fallback(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/v1/sys/mounts/"):
                return httpx.Response(200, json={"data": {"type": "transit/"}})
            assert request.url.path == "/v1/tokenization/encrypt/default"
            return httpx.Response(200, json={"data": {"ciphertext": "vault:v1:abc"}})

        client = _client(handler, engine="auto")
        result = await client.encode("123-45-6789")

        assert result.ok and result.value == "vault:v1:abc"
        assert result.engine == "transit"
        await client.close()

    @pytest.mark.asyncio
    async def test_missing_mount_fails_closed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/v1/sys/mounts/"):
                return httpx.Response(404, json={"errors": ["no matching mount"]})
            raise AssertionError("encode must not be attempted without an engine")

        client = _client(handler, engine="auto")
        result = await client.encode("123-45-6789")

        assert not result.ok
        assert result.error == "vault_engine_unavailable"
        assert client.breaker_state == "closed"  # 1 failure < fail_max
        await client.close()

    @pytest.mark.asyncio
    async def test_unknown_mount_type_fails(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"type": "kv-v2"}})

        client = _client(handler, engine="auto")
        result = await client.encode("x")

        assert not result.ok
        assert result.error == "vault_engine_unavailable"
        await client.close()


class TestEncode:
    @pytest.mark.asyncio
    async def test_tokenization_encode(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["headers"] = dict(request.headers)
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"data": {"token": "tok-42"}})

        client = _client(handler, engine="tokenization")
        result = await client.encode("123-45-6789")

        assert result.ok and result.token == "tok-42"
        assert result.engine == "tokenization"
        assert seen["path"] == "/v1/tokenization/encode/default"
        assert base64.b64decode(seen["body"]["plaintext"]) == b"123-45-6789"
        assert seen["headers"]["x-vault-token"] == "test-root-token"
        assert "x-vault-namespace" not in seen["headers"]
        await client.close()

    @pytest.mark.asyncio
    async def test_transit_encode_is_convergent_with_context(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"data": {"ciphertext": "vault:v2:x"}})

        client = _client(handler, engine="transit")
        result = await client.encode("my@email.ru", context="tenant-a")

        assert result.ok and result.value == "vault:v2:x"
        assert seen["path"] == "/v1/tokenization/encrypt/default"
        assert seen["body"]["convergent_encryption"] is True
        assert base64.b64decode(seen["body"]["context"]) == b"tenant-a"
        # same plaintext+context must map to the same ciphertext server-side;
        # the client's payload is deterministic (encode twice -> identical body)
        body_first = json.dumps(seen["body"], sort_keys=True)
        await client.encode("my@email.ru", context="tenant-a")
        assert json.dumps(seen["body"], sort_keys=True) == body_first
        await client.close()

    @pytest.mark.asyncio
    async def test_namespace_header_when_configured(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = dict(request.headers)
            return httpx.Response(200, json={"data": {"token": "tok"}})

        client = _client(handler, engine="tokenization", namespace="org/tenant-a")
        assert (await client.encode("v")).ok
        assert seen["headers"]["x-vault-namespace"] == "org/tenant-a"
        await client.close()

    @pytest.mark.asyncio
    async def test_vault_errors_are_surfaced(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"errors": ["permission denied"]})

        client = _client(handler, engine="tokenization")
        result = await client.encode("123-45-6789")

        assert not result.ok
        assert "permission denied" in (result.error or "")
        assert client.breaker_state == "closed"
        await client.close()

    @pytest.mark.asyncio
    async def test_transport_error_reports_unreachable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out", request=request)

        client = _client(handler, engine="tokenization")
        result = await client.encode("123-45-6789")

        assert not result.ok
        assert result.error == "vault_unreachable"
        assert client.breaker_state == "closed"  # 1 failure < fail_max
        await client.close()

    @pytest.mark.asyncio
    async def test_empty_token_is_an_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"token": ""}})

        client = _client(handler, engine="tokenization")
        result = await client.encode("x")

        assert not result.ok
        assert result.error == "vault_empty_token"
        await client.close()


class TestDecode:
    @pytest.mark.asyncio
    async def test_tokenization_decode(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            plaintext = base64.b64encode(b"123-45-6789").decode()
            return httpx.Response(200, json={"data": {"plaintext": plaintext}})

        client = _client(handler, engine="tokenization")
        result = await client.decode("tok-42")

        assert result.ok and result.value == "123-45-6789"
        assert seen["path"] == "/v1/tokenization/decode/default"
        assert seen["body"] == {"token": "tok-42"}
        await client.close()

    @pytest.mark.asyncio
    async def test_transit_decode_with_context(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            plaintext = base64.b64encode(b"my@email.ru").decode()
            return httpx.Response(200, json={"data": {"plaintext": plaintext}})

        client = _client(handler, engine="transit")
        result = await client.decode("vault:v1:x", context="tenant-a")

        assert result.ok and result.value == "my@email.ru"
        assert seen["path"] == "/v1/tokenization/decrypt/default"
        assert seen["body"]["ciphertext"] == "vault:v1:x"
        assert base64.b64decode(seen["body"]["context"]) == b"tenant-a"
        await client.close()

    @pytest.mark.asyncio
    async def test_missing_plaintext_is_an_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {}})

        client = _client(handler, engine="tokenization")
        result = await client.decode("tok-42")

        assert not result.ok
        assert result.error == "vault_empty_plaintext"
        await client.close()

    @pytest.mark.asyncio
    async def test_bad_plaintext_is_an_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"plaintext": "not-base64!!"}})

        client = _client(handler, engine="tokenization")
        result = await client.decode("tok-42")

        assert not result.ok
        assert result.error == "vault_bad_plaintext"
        await client.close()


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_opens_after_fail_max_and_short_circuits(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(500, json={"errors": ["internal"]})

        client = _client(handler, engine="tokenization")
        for _ in range(5):
            assert not (await client.encode("x")).ok
        assert client.breaker_state == "open"

        result = await client.encode("x")
        assert result.error == "circuit_open"
        assert calls == 5  # short-circuited: transport not hit

        # decode honours the same breaker
        assert (await client.decode("tok")).error == "circuit_open"
        await client.close()

    @pytest.mark.asyncio
    async def test_half_open_probe_and_recovery(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        client = _client(handler, engine="tokenization")
        for _ in range(5):
            await client.encode("x")
        assert client.breaker_state == "open"

        # advance past reset_timeout -> half-open probe allowed
        client._breaker._opened_at = time.monotonic() - 31
        assert client.breaker_state == "half_open"

        def healthy(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"token": "tok"}})

        client._client = httpx.AsyncClient(transport=httpx.MockTransport(healthy))
        result = await client.encode("x")
        assert result.ok
        assert client.breaker_state == "closed"
        await client.close()

    @pytest.mark.asyncio
    async def test_transport_errors_open_breaker(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        client = _client(handler, engine="tokenization")
        for _ in range(5):
            assert not (await client.encode("x")).ok
        assert client.breaker_state == "open"
        assert (await client.encode("x")).error == "circuit_open"
        await client.close()


class TestSingletonLifecycle:
    def test_init_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "vault_url", "")
        assert init_vault_client() is None
        assert get_vault_client() is None

    @pytest.mark.asyncio
    async def test_init_creates_client_when_configured(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "vault_url", "http://vault:8200")
        monkeypatch.setattr(settings, "vault_token", "tok")
        monkeypatch.setattr(settings, "vault_secret_mount", "tokenization")
        try:
            client = init_vault_client()
            assert client is not None
            assert get_vault_client() is client
            assert client.vault_url == "http://vault:8200"
        finally:
            await close_vault_client()
            assert get_vault_client() is None

    def test_set_get_roundtrip(self):
        client = _client(lambda request: httpx.Response(200, json={}), engine="tokenization")
        set_vault_client(client)
        try:
            assert get_vault_client() is client
        finally:
            set_vault_client(None)

    @pytest.mark.asyncio
    async def test_close_without_client_is_noop(self):
        set_vault_client(None)
        await close_vault_client()  # must not raise
        assert get_vault_client() is None
