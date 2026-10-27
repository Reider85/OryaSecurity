"""Unit tests for the OPA client (EP-12/Prompt 28): evaluation, version
manifest fetch, circuit breaker, fail-open degrade. Transport is mocked via
httpx.MockTransport — no live OPA needed (live coverage lives in
tests/policies/test_bundle_deployment.py)."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from app.config import settings
from app.core.pdp.opa_client import OPAClient, OPADecision, close_opa_client, get_opa_client, init_opa_client, set_opa_client


def _client(handler, **kwargs) -> OPAClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return OPAClient(
        opa_url="http://opa.test:8181",
        bundle_service_url=kwargs.pop("bundle_service_url", "http://bundle.test:8081"),
        timeout_ms=kwargs.pop("timeout_ms", 200),
        client=http,
        **kwargs,
    )


class TestEvaluate:
    @pytest.mark.asyncio
    async def test_allow(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"result": {"allow": True, "deny": []}})

        client = _client(handler)
        decision = await client.evaluate({"detectors": {}})

        assert decision.ok
        assert decision.allowed is True
        assert decision.deny_reasons == []
        assert seen["path"] == "/v1/data/tenant_default"
        assert seen["body"]["input"] == {"detectors": {}}
        await client.close()

    @pytest.mark.asyncio
    async def test_deny_with_reasons(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "result": {
                        "allow": False,
                        "deny": ["Blocked: prompt injection (confidence 0.90)"],
                    }
                },
            )

        client = _client(handler)
        decision = await client.evaluate({})

        assert decision.ok
        assert decision.allowed is False
        assert decision.deny_reasons == ["Blocked: prompt injection (confidence 0.90)"]
        await client.close()

    @pytest.mark.asyncio
    async def test_http_error_records_failure(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        client = _client(handler)
        decision = await client.evaluate({})

        assert not decision.ok
        assert "opa_unavailable" in decision.error
        assert client.breaker_state == "closed"  # 1 failure < fail_max
        await client.close()

    @pytest.mark.asyncio
    async def test_unexpected_payload_is_decode_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"result": {"something": "else"}})

        client = _client(handler)
        decision = await client.evaluate({})

        assert decision.error == "unexpected_opa_result"
        assert client.breaker_state == "closed"
        await client.close()


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_opens_after_fail_max_and_short_circuits(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(500)

        client = _client(handler)
        for _ in range(5):
            assert not (await client.evaluate({})).ok
        assert client.breaker_state == "open"

        decision = await client.evaluate({})
        assert decision.error == "circuit_open"
        assert calls == 5  # short-circuited: transport not hit
        await client.close()

    @pytest.mark.asyncio
    async def test_half_open_probe_and_recovery(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        client = _client(handler)
        for _ in range(5):
            await client.evaluate({})
        assert client.breaker_state == "open"

        # advance past reset_timeout -> half-open probe allowed
        client._breaker._opened_at = time.monotonic() - 31
        assert client.breaker_state == "half_open"

        def healthy(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"result": {"allow": True, "deny": []}})

        client._client = httpx.AsyncClient(transport=httpx.MockTransport(healthy))
        decision = await client.evaluate({})
        assert decision.ok
        assert client.breaker_state == "closed"
        await client.close()


class TestActivePolicyVersion:
    @pytest.mark.asyncio
    async def test_fetches_version_from_manifest(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            assert request.url.path == "/bundles/manifest.json"
            return httpx.Response(200, json={"version": "1.2.3"})

        client = _client(handler)
        assert await client.get_active_policy_version() == "1.2.3"
        # in-process TTL cache: second call does not re-fetch
        assert await client.get_active_policy_version() == "1.2.3"
        assert calls == 1
        await client.close()

    @pytest.mark.asyncio
    async def test_fallback_on_manifest_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        client = _client(handler)
        version = await client.get_active_policy_version()
        assert version == settings.policy_version
        await client.close()

    @pytest.mark.asyncio
    async def test_fallback_when_bundle_service_not_configured(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("must not be called")

        client = _client(handler, bundle_service_url="")
        assert await client.get_active_policy_version() == settings.policy_version
        await client.close()

    @pytest.mark.asyncio
    async def test_invalidate_version_cache(self):
        versions = iter(["1.0.0", "2.0.0"])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"version": next(versions)})

        client = _client(handler)
        assert await client.get_active_policy_version() == "1.0.0"
        client.invalidate_version_cache()
        assert await client.get_active_policy_version() == "2.0.0"
        await client.close()


class TestSingletonLifecycle:
    def test_init_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "pdp_opa_enabled", False)
        assert init_opa_client() is None
        assert get_opa_client() is None

    @pytest.mark.asyncio
    async def test_init_creates_client_when_enabled(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "pdp_opa_enabled", True)
        monkeypatch.setattr(settings, "opa_url", "http://opa:8181")
        try:
            client = init_opa_client()
            assert client is not None
            assert get_opa_client() is client
        finally:
            await close_opa_client()
            assert get_opa_client() is None

    def test_set_get_roundtrip(self):
        client = _client(lambda request: httpx.Response(200, json={}))
        set_opa_client(client)
        try:
            assert get_opa_client() is client
        finally:
            set_opa_client(None)
