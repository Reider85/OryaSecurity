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
