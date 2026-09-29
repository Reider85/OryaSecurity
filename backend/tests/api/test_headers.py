"""Tests for the centralized X-Scanner-* response headers.

These tests avoid Postgres and Redis by overriding the API-key dependency and
swapping the decision cache for a fake, which keeps the suite runnable on a
developer machine with no backing services.
"""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api import openai_compat as openai_api
from app.api import scan as scan_api
from app.core.auth import AuthInfo, verify_api_key
from app.core.headers import (
    HEADER_CACHE,
    HEADER_LATENCY,
    HEADER_REASON,
    HEADER_REQUEST_ID,
    HEADER_VERDICT,
    MAX_REASON_LENGTH,
    ScannerHeadersMiddleware,
    scanner_headers,
    set_scanner_context,
)
from app.core.proxy import LLMProxyError
from app.main import app

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class FakeCache:
    """Decision cache double. Returns a miss unless ``entry`` is provided."""

    def __init__(self, entry: dict | None = None) -> None:
        self.entry = entry

    async def get(self, prompt: str):
        if self.entry is not None:
            return dict(self.entry), True
        return None, False

    async def set(self, prompt: str, verdict: str, reason: str, rules_matched: list[dict]) -> None:
        if self.entry is None:
            self.entry = {
                "verdict": verdict,
                "reason": reason,
                "rules_matched": rules_matched,
                "ts": 0.0,
            }


@pytest.fixture
def auth_override():
    app.dependency_overrides[verify_api_key] = lambda: AuthInfo(
        api_key="test-key", tenant_id="tenant-a"
    )
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def no_audit(monkeypatch: pytest.MonkeyPatch):
    """Prevent audit writes from touching the database."""

    async def _noop(**kwargs) -> None:
        return None

    monkeypatch.setattr(scan_api, "write_event", _noop)


@pytest.fixture
def miss_cache(monkeypatch: pytest.MonkeyPatch):
    cache = FakeCache()
    monkeypatch.setattr(scan_api, "decision_cache", cache)
    return cache


@pytest.fixture
def test_client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


class TestScanHeaders:
    def test_all_scanner_headers_present_on_scan(
        self, test_client: TestClient, auth_override, miss_cache, no_audit
    ) -> None:
        resp = test_client.post("/scan", json={"prompt": "hello world"})

        assert resp.status_code == 200
        for header in (HEADER_VERDICT, HEADER_REASON, HEADER_LATENCY, HEADER_REQUEST_ID, HEADER_CACHE):
            assert header in resp.headers, f"missing {header}"

    def test_verdict_header_allow(self, test_client: TestClient, auth_override, miss_cache, no_audit) -> None:
        resp = test_client.post("/scan", json={"prompt": "hello world"})

        assert resp.headers[HEADER_VERDICT] == "allow"

    def test_verdict_header_block(
        self, test_client: TestClient, auth_override, miss_cache, no_audit
    ) -> None:
        resp = test_client.post("/scan", json={"prompt": "My SSN is 123-45-6789"})

        assert resp.status_code == 200
        assert resp.headers[HEADER_VERDICT] == "block"

    def test_cache_header_miss_on_first_request(
        self, test_client: TestClient, auth_override, miss_cache, no_audit
    ) -> None:
        resp = test_client.post("/scan", json={"prompt": "cache me"})

        assert resp.headers[HEADER_CACHE] == "MISS"

    def test_cache_header_hit_on_second_request(
        self, test_client: TestClient, auth_override, miss_cache, no_audit
    ) -> None:
        prompt = "a prompt that will be cached"
        first = test_client.post("/scan", json={"prompt": prompt})
        second = test_client.post("/scan", json={"prompt": prompt})

        assert first.headers[HEADER_CACHE] == "MISS"
        assert second.headers[HEADER_CACHE] == "HIT"

    def test_request_id_header_is_uuid4(
        self, test_client: TestClient, auth_override, miss_cache, no_audit
    ) -> None:
        resp = test_client.post("/scan", json={"prompt": "hello"})

        value = resp.headers[HEADER_REQUEST_ID]
        assert UUID_RE.match(value), f"not a v4 UUID: {value}"
        assert uuid.UUID(value).version == 4

    def test_request_id_header_matches_body(
        self, test_client: TestClient, auth_override, miss_cache, no_audit
    ) -> None:
        resp = test_client.post("/scan", json={"prompt": "hello"})

        assert resp.headers[HEADER_REQUEST_ID] == resp.json()["request_id"]

    def test_request_id_unique_per_request(
        self, test_client: TestClient, auth_override, miss_cache, no_audit
    ) -> None:
        first = test_client.post("/scan", json={"prompt": "hello"})
        second = test_client.post("/scan", json={"prompt": "hello"})

        assert first.headers[HEADER_REQUEST_ID] != second.headers[HEADER_REQUEST_ID]

    def test_latency_header_is_non_negative_number(
        self, test_client: TestClient, auth_override, miss_cache, no_audit
    ) -> None:
        resp = test_client.post("/scan", json={"prompt": "hello"})

        assert float(resp.headers[HEADER_LATENCY]) >= 0

    def test_reason_header_matches_body(
        self, test_client: TestClient, auth_override, miss_cache, no_audit
    ) -> None:
        resp = test_client.post("/scan", json={"prompt": "hello"})

        assert resp.headers[HEADER_REASON] == resp.json()["reason"]

    def test_block_reason_header_set(
        self, test_client: TestClient, auth_override, miss_cache, no_audit
    ) -> None:
        resp = test_client.post("/scan", json={"prompt": "My SSN is 123-45-6789"})

        assert resp.headers[HEADER_REASON]


class TestOpenAICompatHeaders:
    @pytest.fixture
    def allow_proxy(self, monkeypatch: pytest.MonkeyPatch):
        from app.models.openai import (
            ChatCompletionChoice,
            ChatCompletionMessage,
            ChatCompletionResponse,
        )

        async def _fake_proxy_chat(payload):
            return ChatCompletionResponse(
                id="chatcmpl-test",
                object="chat.completion",
                created=0,
                model=payload.model,
                choices=[
                    ChatCompletionChoice(
                        index=0,
                        message=ChatCompletionMessage(role="assistant", content="hi"),
                        finish_reason="stop",
                    )
                ],
            )

        monkeypatch.setattr(openai_api, "proxy_chat", _fake_proxy_chat)
        return _fake_proxy_chat

    def test_verdict_allow_header(
        self, test_client: TestClient, auth_override, allow_proxy
    ) -> None:
        resp = test_client.post(
            "/v1/chat/completions",
            json={"model": "gpt-test", "messages": [{"role": "user", "content": "hello"}]},
        )

        assert resp.status_code == 200
        assert resp.headers[HEADER_VERDICT] == "allow"
        assert resp.headers[HEADER_REQUEST_ID]
        assert resp.headers[HEADER_LATENCY]

    def test_block_error_path_sets_verdict_and_reason(
        self, test_client: TestClient, auth_override, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _blocking_proxy(payload):
            raise LLMProxyError(status_code=403, detail="Blocked by rules: pii_ssn_us")

        monkeypatch.setattr(openai_api, "proxy_chat", _blocking_proxy)

        resp = test_client.post(
            "/v1/chat/completions",
            json={"model": "gpt-test", "messages": [{"role": "user", "content": "My SSN is 123-45-6789"}]},
        )

        assert resp.status_code == 403
        assert resp.headers[HEADER_VERDICT] == "block"
        assert resp.headers[HEADER_REASON] == "Blocked by rules: pii_ssn_us"
        assert resp.headers[HEADER_REQUEST_ID]

    def test_upstream_error_is_not_reported_as_block(
        self, test_client: TestClient, auth_override, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 502 means the LLM was unreachable, not that policy blocked it."""

        async def _failing_proxy(payload):
            raise LLMProxyError(status_code=502, detail="LLM provider timeout")

        monkeypatch.setattr(openai_api, "proxy_chat", _failing_proxy)

        resp = test_client.post(
            "/v1/chat/completions",
            json={"model": "gpt-test", "messages": [{"role": "user", "content": "hello"}]},
        )

        assert resp.status_code == 502
        assert resp.headers[HEADER_VERDICT] == "allow"

    def test_rate_limit_headers_preserved(
        self, test_client: TestClient, auth_override, allow_proxy
    ) -> None:
        resp = test_client.post(
            "/v1/chat/completions",
            json={"model": "gpt-test", "messages": [{"role": "user", "content": "hello"}]},
        )

        assert "X-RateLimit-Limit" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers


class TestNonScannerEndpoints:
    def test_health_has_request_id_and_latency(self, test_client: TestClient) -> None:
        resp = test_client.get("/health")

        assert resp.status_code in (200, 503)
        assert resp.headers[HEADER_REQUEST_ID]
        assert HEADER_LATENCY in resp.headers

    def test_health_has_no_verdict_header(self, test_client: TestClient) -> None:
        """A health check made no decision, so it must not claim a verdict."""
        resp = test_client.get("/health")

        assert HEADER_VERDICT not in resp.headers
        assert HEADER_CACHE not in resp.headers

    def test_metrics_has_request_id_and_latency(self, test_client: TestClient) -> None:
        resp = test_client.get("/metrics")

        assert resp.status_code == 200
        assert resp.headers[HEADER_REQUEST_ID]
        assert HEADER_LATENCY in resp.headers

    def test_metrics_has_no_verdict_header(self, test_client: TestClient) -> None:
        resp = test_client.get("/metrics")

        assert HEADER_VERDICT not in resp.headers

    def test_unknown_route_still_gets_request_id(self, test_client: TestClient) -> None:
        resp = test_client.get("/definitely-not-a-route")

        assert resp.status_code == 404
        assert resp.headers[HEADER_REQUEST_ID]

    def test_openapi_schema_has_request_id(self, test_client: TestClient) -> None:
        resp = test_client.get("/openapi.json")

        assert resp.status_code == 200
        assert resp.headers[HEADER_REQUEST_ID]


@pytest.fixture
def scanner_route():
    """Register a temporary endpoint that records scanner context.

    Routes are appended to the shared app, so they are removed afterwards to
    keep the module from leaking state into other test files.
    """
    created: list[str] = []

    def _register(path: str, **context):
        async def _endpoint(request: Request):
            set_scanner_context(request, **context)
            return {"ok": True}

        app.get(path)(_endpoint)
        created.append(path)
        return f"/{path.lstrip('/')}"

    yield _register

    for path in created:
        app.router.routes = [
            r for r in app.router.routes if getattr(r, "path", None) != path
        ]


class TestSetScannerContext:
    def test_verdict_normalized_to_lowercase(
        self, test_client: TestClient, scanner_route
    ) -> None:
        path = scanner_route("/_test/upper", verdict="BLOCK", reason="because")

        resp = test_client.get(path)

        assert resp.headers[HEADER_VERDICT] == "block"

    def test_newlines_stripped_from_reason(
        self, test_client: TestClient, scanner_route
    ) -> None:
        path = scanner_route(
            "/_test/newline", verdict="allow", reason="line one\nline two\r\nline three"
        )

        resp = test_client.get(path)

        reason = resp.headers[HEADER_REASON]
        assert "\n" not in reason
        assert "\r" not in reason
        assert reason == "line one line two line three"

    def test_long_reason_truncated(
        self, test_client: TestClient, scanner_route
    ) -> None:
        path = scanner_route("/_test/long", verdict="allow", reason="x" * 5000)

        resp = test_client.get(path)

        assert len(resp.headers[HEADER_REASON]) == MAX_REASON_LENGTH

    def test_non_latin1_reason_does_not_break_response(
        self, test_client: TestClient, scanner_route
    ) -> None:
        """Cyrillic rule names must not turn a 200 into a 500.

        Starlette encodes header values as latin-1 and raises otherwise, so the
        reason has to be sanitized before it reaches the response.
        """
        path = scanner_route(
            "/_test/cyrillic", verdict="block", reason="Правило: паспорт РФ"
        )

        resp = test_client.get(path)

        assert resp.status_code == 200
        assert resp.headers[HEADER_VERDICT] == "block"
        assert resp.headers[HEADER_REASON]
        assert "\u041f" not in resp.headers[HEADER_REASON]

    def test_cache_value_normalized(
        self, test_client: TestClient, scanner_route
    ) -> None:
        path = scanner_route("/_test/cache", verdict="allow", cache="hit")

        resp = test_client.get(path)

        assert resp.headers[HEADER_CACHE] == "HIT"

    def test_unknown_cache_value_becomes_miss(
        self, test_client: TestClient, scanner_route
    ) -> None:
        path = scanner_route("/_test/cache2", verdict="allow", cache="weird")

        resp = test_client.get(path)

        assert resp.headers[HEADER_CACHE] == "MISS"

    def test_partial_context_only_sets_provided_headers(
        self, test_client: TestClient, scanner_route
    ) -> None:
        path = scanner_route("/_test/partial", verdict="allow")

        resp = test_client.get(path)

        assert resp.headers[HEADER_VERDICT] == "allow"
        assert HEADER_REASON not in resp.headers
        assert HEADER_CACHE not in resp.headers

    def test_endpoint_latency_wins_over_total_elapsed(
        self, test_client: TestClient, scanner_route
    ) -> None:
        path = scanner_route("/_test/latency", verdict="allow", latency_ms=1.5)

        resp = test_client.get(path)

        assert float(resp.headers[HEADER_LATENCY]) == 1.5


class TestScannerHeadersHelper:
    def test_builds_expected_headers(self) -> None:
        headers = scanner_headers(
            verdict="block",
            reason="Blocked by rules: pii_ssn_us",
            latency_ms=2.25,
            request_id="11111111-1111-4111-8111-111111111111",
            cache="MISS",
        )

        assert headers[HEADER_VERDICT] == "block"
        assert headers[HEADER_REASON] == "Blocked by rules: pii_ssn_us"
        assert headers[HEADER_LATENCY] == "2.25"
        assert headers[HEADER_REQUEST_ID] == "11111111-1111-4111-8111-111111111111"
        assert headers[HEADER_CACHE] == "MISS"

    def test_omits_unset_optional_headers(self) -> None:
        headers = scanner_headers(verdict="allow")

        assert headers == {HEADER_VERDICT: "allow"}


class TestMiddlewareRegistration:
    def test_middleware_installed_on_app(self) -> None:
        installed = [m.cls for m in app.user_middleware]
        assert ScannerHeadersMiddleware in installed

    def test_middleware_keeps_existing_headers(self, test_client: TestClient) -> None:
        resp = test_client.get("/health")

        assert resp.headers["X-Scanner-Status"] in ("ok", "degraded", "down")
        assert resp.headers[HEADER_REQUEST_ID]
