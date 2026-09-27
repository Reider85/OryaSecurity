from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from app.core.cache import decision_cache
from app.models.openai import (
    ChatCompletionChoice,
    ChatCompletionMessage,
    ChatCompletionResponse,
    ChatCompletionUsage,
)


@pytest.fixture(autouse=True)
def clear_cache():
    decision_cache.flush()
    yield
    decision_cache.flush()


def _make_response_payload(content: str = "Hello!") -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "gpt-3.5-turbo",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class TestChatCompletionsEndpoint:
    def test_missing_auth(self, client):
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
        assert response.status_code == 401

    def test_invalid_auth(self, client):
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={"Authorization": "Bearer invalid-key"},
        )
        assert response.status_code == 401

    def test_clean_prompt_allowed(self, client, auth_headers):
        with patch("app.api.openai_compat.proxy_chat") as mock_proxy:
            mock_proxy.return_value = ChatCompletionResponse(
                id="chatcmpl-test",
                object="chat.completion",
                created=int(time.time()),
                model="gpt-3.5-turbo",
                choices=[
                    ChatCompletionChoice(
                        index=0,
                        message=ChatCompletionMessage(role="assistant", content="Hello!"),
                        finish_reason="stop",
                    )
                ],
                usage=ChatCompletionUsage(),
            )
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": "Hello"}],
                },
                headers=auth_headers,
            )
            assert response.status_code == 200
            assert response.headers["X-Scanner-Verdict"] == "allow"
            assert "X-Request-Id" in response.headers

    def test_blocked_prompt_returns_403(self, client, auth_headers):
        from app.core.proxy import LLMProxyError

        with patch("app.api.openai_compat.proxy_chat") as mock_proxy:
            mock_proxy.side_effect = LLMProxyError(403, "Blocked by rules: pii_ssn_us")
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": "My SSN is 123-45-6789"}],
                },
                headers=auth_headers,
            )
            assert response.status_code == 403
            assert response.headers["X-Scanner-Verdict"] == "block"
            assert "pii_ssn_us" in response.json()["error"]["message"]

    def test_request_id_in_response(self, client, auth_headers):
        with patch("app.api.openai_compat.proxy_chat") as mock_proxy:
            mock_proxy.return_value = ChatCompletionResponse(
                id="chatcmpl-test",
                object="chat.completion",
                created=int(time.time()),
                model="gpt-3.5-turbo",
                choices=[
                    ChatCompletionChoice(
                        index=0,
                        message=ChatCompletionMessage(role="assistant", content="Hi"),
                        finish_reason="stop",
                    )
                ],
                usage=ChatCompletionUsage(),
            )
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": "Hi"}],
                },
                headers=auth_headers,
            )
            assert "X-Request-Id" in response.headers
            assert len(response.headers["X-Request-Id"]) == 36  # UUID format

    def test_invalid_request_body(self, client, auth_headers):
        response = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-3.5-turbo"},
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_empty_messages_rejected(self, client, auth_headers):
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-3.5-turbo",
                "messages": [],
            },
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_rate_limit_within_limit(self, client, auth_headers):
        """Test that requests within rate limit are allowed."""
        with patch("app.api.openai_compat.proxy_chat") as mock_proxy:
            mock_proxy.return_value = ChatCompletionResponse(
                id="chatcmpl-test",
                object="chat.completion",
                created=int(time.time()),
                model="gpt-3.5-turbo",
                choices=[
                    ChatCompletionChoice(
                        index=0,
                        message=ChatCompletionMessage(role="assistant", content="Hello"),
                        finish_reason="stop",
                    )
                ],
                usage=ChatCompletionUsage(),
            )
            
            # Should allow 100 requests (within limit)
            for _ in range(10):  # Testing with 10 instead of 100 for speed
                response = client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "gpt-3.5-turbo",
                        "messages": [{"role": "user", "content": "Test"}],
                    },
                    headers=auth_headers,
                )
                assert response.status_code == 200
                assert response.headers["X-RateLimit-Limit"] == "100"
                assert int(response.headers["X-RateLimit-Remaining"]) >= 90

    def test_rate_limit_exceeded(self, client, auth_headers):
        """Test that requests exceeding rate limit return 429."""
        with patch("app.api.openai_compat.proxy_chat") as mock_proxy:
            mock_proxy.return_value = ChatCompletionResponse(
                id="chatcmpl-test",
                object="chat.completion",
                created=int(time.time()),
                model="gpt-3.5-turbo",
                choices=[
                    ChatCompletionChoice(
                        index=0,
                        message=ChatCompletionMessage(role="assistant", content="Hello"),
                        finish_reason="stop",
                    )
                ],
                usage=ChatCompletionUsage(),
            )
            
            # Use up 5 requests (testing with lower limit for speed)
            for _ in range(5):
                response = client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "gpt-3.5-turbo",
                        "messages": [{"role": "user", "content": "Test"}],
                    },
                    headers=auth_headers,
                )
                assert response.status_code == 200
            
            # 6th request should be rate limited
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": "Test"}],
                },
                headers=auth_headers,
            )
            assert response.status_code == 429
            assert response.headers["X-RateLimit-Limit"] == "100"
            assert response.headers["X-RateLimit-Remaining"] == "0"
            assert response.headers["Retry-After"] == "1"
            assert "Rate limit exceeded" in response.json()["error"]["message"]

    def test_rate_limit_headers_present(self, client, auth_headers):
        """Test that rate limit headers are present in responses."""
        with patch("app.api.openai_compat.proxy_chat") as mock_proxy:
            mock_proxy.return_value = ChatCompletionResponse(
                id="chatcmpl-test",
                object="chat.completion",
                created=int(time.time()),
                model="gpt-3.5-turbo",
                choices=[
                    ChatCompletionChoice(
                        index=0,
                        message=ChatCompletionMessage(role="assistant", content="Hello"),
                        finish_reason="stop",
                    )
                ],
                usage=ChatCompletionUsage(),
            )
            
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": "Hello"}],
                },
                headers=auth_headers,
            )
            
            # Check rate limit headers
            assert "X-RateLimit-Limit" in response.headers
            assert "X-RateLimit-Remaining" in response.headers
            assert response.headers["X-RateLimit-Limit"] == "100"
            assert int(response.headers["X-RateLimit-Remaining"]) >= 99

    def test_different_keys_independent_limits(self, client, auth_headers):
        """Test that different API keys have independent rate limits."""
        # Create second auth header with different key
        second_auth_headers = {"Authorization": "Bearer second-api-key"}
        
        with patch("app.api.openai_compat.proxy_chat") as mock_proxy:
            mock_proxy.return_value = ChatCompletionResponse(
                id="chatcmpl-test",
                object="chat.completion",
                created=int(time.time()),
                model="gpt-3.5-turbo",
                choices=[
                    ChatCompletionChoice(
                        index=0,
                        message=ChatCompletionMessage(role="assistant", content="Hello"),
                        finish_reason="stop",
                    )
                ],
                usage=ChatCompletionUsage(),
            )
            
            # Use up limit for first key
            for _ in range(5):
                response = client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "gpt-3.5-turbo",
                        "messages": [{"role": "user", "content": "Test"}],
                    },
                    headers=auth_headers,
                )
                assert response.status_code == 200
            
            # First key should now be rate limited
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": "Test"}],
                },
                headers=auth_headers,
            )
            assert response.status_code == 429
            
            # Second key should still work
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": "Test"}],
                },
                headers=second_auth_headers,
            )
            assert response.status_code == 200
