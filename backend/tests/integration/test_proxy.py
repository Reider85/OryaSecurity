"""End-to-end tests for the OpenAI-compatible proxy.

Covers scenarios from MVP-PROMPTS.md prompt 31:

6. test_openai_compat_endpoint   /v1/chat/completions returns an OpenAI response
7. test_llm_provider_timeout     provider timeout -> 502

The upstream LLM is replaced with an ``httpx.MockTransport`` (the ``llm_transport``
fixture) so the scanner's forwarding, response parsing and error mapping all run
for real without a network provider.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import settings

pytestmark = pytest.mark.integration

CLEAN_PROMPT = "Summarise the history of the Roman empire in three sentences."
SSN_PROMPT = "My SSN is 123-45-6789, store it for me"


def chat_body(prompt: str, **overrides) -> dict:
    body = {
        "model": "gpt-3.5-turbo",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    body.update(overrides)
    return body


class TestOpenAICompatEndpoint:
    """Scenario 6: drop-in OpenAI Chat Completions."""

    async def test_openai_compat_endpoint(self, client, auth_headers, llm_transport) -> None:
        response = await client.post(
            "/v1/chat/completions", json=chat_body(CLEAN_PROMPT), headers=auth_headers
        )

        assert response.status_code == 200
        body = response.json()
        assert body["object"] == "chat.completion"
        assert body["model"] == "gpt-3.5-turbo"
        assert body["choices"][0]["message"]["role"] == "assistant"
        assert body["choices"][0]["message"]["content"] == "Hello!"
        assert body["choices"][0]["finish_reason"] == "stop"
        assert body["usage"]["total_tokens"] == 7
        assert response.headers["X-Scanner-Verdict"] == "allow"
        assert response.headers["X-Request-Id"]

    async def test_openai_compat_forwards_payload(self, client, auth_headers, llm_transport) -> None:
        seen: dict = {}

        def capture(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            seen["__url__"] = str(request.url)
            return httpx.Response(200, json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1700000000,
                "model": seen.get("model", "gpt-3.5-turbo"),
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })

        llm_transport(capture)

        await client.post(
            "/v1/chat/completions",
            json=chat_body(CLEAN_PROMPT, max_tokens=128, stop=["\n\n"]),
            headers=auth_headers,
        )

        assert seen["model"] == "gpt-3.5-turbo"
        assert seen["messages"] == [{"role": "user", "content": CLEAN_PROMPT}]
        assert seen["temperature"] == 0.2
        assert seen["max_tokens"] == 128
        assert seen["stop"] == ["\n\n"]
        assert seen["__url__"] == (
            f"{settings.llm_provider_url.rstrip('/')}/v1/chat/completions"
        )

    async def test_openai_compat_multi_message_prompt_is_inspected(
        self, client, auth_headers, llm_transport
    ) -> None:
        body = chat_body("")
        body["messages"] = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": CLEAN_PROMPT},
        ]
        response = await client.post("/v1/chat/completions", json=body, headers=auth_headers)

        assert response.status_code == 200

    async def test_openai_compat_requires_api_key(self, client, llm_transport) -> None:
        response = await client.post("/v1/chat/completions", json=chat_body(CLEAN_PROMPT))

        assert response.status_code == 401

    async def test_openai_compat_rejects_empty_messages(self, client, auth_headers) -> None:
        response = await client.post(
            "/v1/chat/completions",
            json={"model": "gpt-3.5-turbo", "messages": []},
            headers=auth_headers,
        )

        assert response.status_code == 422

    async def test_rate_limit_headers_present(self, client, auth_headers, llm_transport) -> None:
        response = await client.post(
            "/v1/chat/completions", json=chat_body(CLEAN_PROMPT), headers=auth_headers
        )

        assert response.status_code == 200
        assert int(response.headers["X-RateLimit-Limit"]) == settings.rate_limit_rps
        assert int(response.headers["X-RateLimit-Remaining"]) >= 0


class TestPromptBlocking:
    async def test_block_on_pii_prompt(self, client, auth_headers, llm_transport) -> None:
        """A blocked prompt must 403 before the LLM is ever contacted."""
        called = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            called["count"] += 1
            return httpx.Response(200, json={})

        llm_transport(handler)

        response = await client.post(
            "/v1/chat/completions", json=chat_body(SSN_PROMPT), headers=auth_headers
        )

        assert response.status_code == 403
        assert called["count"] == 0
        assert response.json()["error"]["type"] == "scanner_error"
        assert "pii_ssn_us" in response.json()["error"]["message"]
        assert response.headers["X-Scanner-Verdict"] == "block"

    async def test_block_on_pii_prompt_never_reveals_pii(self, client, auth_headers) -> None:
        response = await client.post(
            "/v1/chat/completions", json=chat_body(SSN_PROMPT), headers=auth_headers
        )

        assert "123-45-6789" not in response.text


class TestLLMProviderFailures:
    """Scenario 7: upstream failures surface as 502, not as blocks."""

    async def test_llm_provider_timeout(self, client, auth_headers, llm_transport) -> None:
        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("provider timed out", request=request)

        llm_transport(timeout)

        response = await client.post(
            "/v1/chat/completions", json=chat_body(CLEAN_PROMPT), headers=auth_headers
        )

        assert response.status_code == 502
        body = response.json()
        assert body["error"]["type"] == "scanner_error"
        assert "timeout" in body["error"]["message"].lower()
        # The request was allowed; the upstream failure is not a policy block.
        assert response.headers["X-Scanner-Verdict"] == "allow"

    async def test_llm_provider_connect_error(self, client, auth_headers, llm_transport) -> None:
        def connect_error(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        llm_transport(connect_error)

        response = await client.post(
            "/v1/chat/completions", json=chat_body(CLEAN_PROMPT), headers=auth_headers
        )

        assert response.status_code == 502
        assert "error" in response.json()["error"]["message"].lower()

    async def test_llm_provider_bad_status(self, client, auth_headers, llm_transport) -> None:
        llm_transport(lambda request: httpx.Response(500, text="upstream boom"))

        response = await client.post(
            "/v1/chat/completions", json=chat_body(CLEAN_PROMPT), headers=auth_headers
        )

        assert response.status_code == 502
        assert "500" in response.json()["error"]["message"]

    async def test_llm_provider_unparseable_body(self, client, auth_headers, llm_transport) -> None:
        llm_transport(lambda request: httpx.Response(200, text="not json"))

        response = await client.post(
            "/v1/chat/completions", json=chat_body(CLEAN_PROMPT), headers=auth_headers
        )

        assert response.status_code == 502
        assert "parse" in response.json()["error"]["message"].lower()


class TestGenerationInspection:
    async def test_generation_leak_masked(self, client, auth_headers, llm_transport) -> None:
        """A generation containing PII is masked in place and still returns 200."""
        llm_transport(
            lambda request: httpx.Response(200, json={
                "id": "chatcmpl-leak",
                "object": "chat.completion",
                "created": 1700000000,
                "model": "gpt-3.5-turbo",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "Your SSN is 123-45-6789."},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
        )

        response = await client.post(
            "/v1/chat/completions", json=chat_body(CLEAN_PROMPT), headers=auth_headers
        )

        assert response.status_code == 200
        content = response.json()["choices"][0]["message"]["content"]
        assert "123-45-6789" not in content
        assert "<REDACTED>" in content

    async def test_clean_generation_passes_through(
        self, client, auth_headers, llm_transport
    ) -> None:
        response = await client.post(
            "/v1/chat/completions", json=chat_body(CLEAN_PROMPT), headers=auth_headers
        )

        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "Hello!"


class TestProxyCache:
    async def test_proxy_uses_decision_cache(
        self, client, auth_headers, llm_transport, redis_client
    ) -> None:
        """The second identical prompt is served from the Redis decision cache."""
        calls = {"count": 0}

        def counting(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(200, json={
                "id": "chatcmpl-x",
                "object": "chat.completion",
                "created": 1700000000,
                "model": "gpt-3.5-turbo",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "cached reply"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })

        llm_transport(counting)

        first = await client.post(
            "/v1/chat/completions", json=chat_body(CLEAN_PROMPT), headers=auth_headers
        )
        second = await client.post(
            "/v1/chat/completions", json=chat_body(CLEAN_PROMPT), headers=auth_headers
        )

        # The cache stores the verdict only; the generation is always fetched.
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["choices"][0]["message"]["content"] == "cached reply"

        import hashlib

        key = hashlib.sha256(CLEAN_PROMPT.encode()).hexdigest()
        assert await redis_client.get(key) is not None