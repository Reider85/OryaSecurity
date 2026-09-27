from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.proxy import (
    LLMProxyError,
    _extract_prompt_text,
    _mask_blocked_content,
    proxy_chat,
)
from app.core.rules.base import RuleMatch
from app.models.openai import (
    ChatCompletionChoice,
    ChatCompletionMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
)
from app.core.cache import decision_cache


@pytest.fixture(autouse=True)
def clear_cache():
    decision_cache.flush()
    yield
    decision_cache.flush()


def _make_llm_response(content: str = "Hello!") -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="chatcmpl-test",
        object="chat.completion",
        created=int(time.time()),
        model="gpt-3.5-turbo",
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatCompletionMessage(role="assistant", content=content),
                finish_reason="stop",
            )
        ],
        usage=ChatCompletionUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def _make_request(prompt: str = "What is the weather?") -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="gpt-3.5-turbo",
        messages=[ChatCompletionMessage(role="user", content=prompt)],
    )


class TestExtractPromptText:
    def test_single_message(self):
        messages = [ChatCompletionMessage(role="user", content="Hello")]
        assert _extract_prompt_text(messages) == "Hello"

    def test_multiple_messages(self):
        messages = [
            ChatCompletionMessage(role="system", content="You are helpful"),
            ChatCompletionMessage(role="user", content="Hi"),
        ]
        result = _extract_prompt_text(messages)
        assert "You are helpful" in result
        assert "Hi" in result

    def test_empty_content(self):
        messages = [ChatCompletionMessage(role="user", content=None)]
        assert _extract_prompt_text(messages) == ""


class TestMaskBlockedContent:
    def test_no_matches(self):
        text = "Hello world"
        assert _mask_blocked_content(text, []) == text

    def test_single_match(self):
        text = "My SSN is 123-45-6789 today"
        match = RuleMatch(
            rule_id="pii_ssn_us",
            rule_name="US SSN",
            value_hash="abc",
            position=(10, 21),
            severity="high",
            action="block",
        )
        result = _mask_blocked_content(text, [match])
        assert "123-45-6789" not in result
        assert "<REDACTED>" in result
        assert result == "My SSN is <REDACTED> today"

    def test_multiple_matches_reverse_order(self):
        text = "Email user@example.com and SSN 123-45-6789"
        matches = [
            RuleMatch("pii_email", "Email", "a", (6, 25), "medium", "block"),
            RuleMatch("pii_ssn_us", "SSN", "b", (30, 41), "high", "block"),
        ]
        result = _mask_blocked_content(text, matches)
        assert "user@example.com" not in result
        assert "123-45-6789" not in result
        assert result.count("<REDACTED>") == 2


class TestProxyChat:
    @pytest.mark.asyncio
    @patch("app.core.proxy._forward_to_llm")
    async def test_allow_clean_prompt(self, mock_forward):
        mock_forward.return_value = _make_llm_response("The weather is nice.")
        request = _make_request("What is the weather?")
        result = await proxy_chat(request)
        assert result.choices[0].message.content == "The weather is nice."
        mock_forward.assert_called_once()

    @pytest.mark.asyncio
    async def test_block_prompt_with_ssn(self):
        request = _make_request("My SSN is 123-45-6789")
        with pytest.raises(LLMProxyError) as exc_info:
            await proxy_chat(request)
        assert exc_info.value.status_code == 403
        assert "pii_ssn_us" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_block_prompt_with_aws_key(self):
        request = _make_request("Key is AKIAIOSFODNN7EXAMPLE")
        with pytest.raises(LLMProxyError) as exc_info:
            await proxy_chat(request)
        assert exc_info.value.status_code == 403
        assert "secret_aws_key" in exc_info.value.detail

    @pytest.mark.asyncio
    @patch("app.core.proxy._forward_to_llm")
    async def test_cache_hit_allow_still_forwards(self, mock_forward):
        mock_forward.return_value = _make_llm_response("Result")
        request = _make_request("What is AI?")
        await proxy_chat(request)
        mock_forward.assert_called_once()

        mock_forward.reset_mock()
        mock_forward.return_value = _make_llm_response("New result")
        result = await proxy_chat(request)
        assert result.choices[0].message.content == "New result"
        mock_forward.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_hit_block_skips_llm(self):
        request = _make_request("My SSN is 123-45-6789")
        with pytest.raises(LLMProxyError) as exc_info:
            await proxy_chat(request)
        assert exc_info.value.status_code == 403

        with pytest.raises(LLMProxyError) as exc_info:
            await proxy_chat(request)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    @patch("app.core.proxy._forward_to_llm")
    async def test_block_generation(self, mock_forward):
        mock_forward.return_value = _make_llm_response(
            "Your SSN is 123-45-6789 in our records."
        )
        request = _make_request("What is my SSN status?")
        result = await proxy_chat(request)
        assert "<REDACTED>" in result.choices[0].message.content
        assert "123-45-6789" not in result.choices[0].message.content

    @pytest.mark.asyncio
    async def test_llm_timeout(self):
        request = _make_request("Hello")
        with patch("app.core.proxy._forward_to_llm") as mock_forward:
            mock_forward.side_effect = LLMProxyError(502, "LLM provider timeout")
            with pytest.raises(LLMProxyError) as exc_info:
                await proxy_chat(request)
            assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    @patch("app.core.proxy._forward_to_llm")
    async def test_clean_generation_passes_through(self, mock_forward):
        mock_forward.return_value = _make_llm_response("All good response.")
        request = _make_request("Simple question")
        result = await proxy_chat(request)
        assert result.choices[0].message.content == "All good response."

    @pytest.mark.asyncio
    @patch("app.core.proxy._forward_to_llm")
    async def test_generation_with_empty_content(self, mock_forward):
        resp = _make_llm_response("")
        resp.choices[0].message.content = None
        mock_forward.return_value = resp
        request = _make_request("Hello")
        result = await proxy_chat(request)
        assert result.choices[0].message.content is None

    @pytest.mark.asyncio
    @patch("app.core.proxy._forward_to_llm")
    async def test_generation_with_no_choices(self, mock_forward):
        resp = _make_llm_response("")
        resp.choices = []
        mock_forward.return_value = resp
        request = _make_request("Hello")
        result = await proxy_chat(request)
        assert result.choices == []


class TestBuildForwardPayload:
    def test_minimal_payload(self):
        from app.core.proxy import _build_forward_payload

        request = _make_request("Hello")
        payload = _build_forward_payload(request)
        assert payload["model"] == "gpt-3.5-turbo"
        assert len(payload["messages"]) == 1
        assert payload["messages"][0]["role"] == "user"

    def test_optional_params_included(self):
        from app.core.proxy import _build_forward_payload

        request = ChatCompletionRequest(
            model="gpt-4",
            messages=[ChatCompletionMessage(role="user", content="Hi")],
            temperature=0.7,
            max_tokens=100,
            top_p=0.9,
            frequency_penalty=0.5,
            presence_penalty=0.3,
            stop=["END"],
        )
        payload = _build_forward_payload(request)
        assert payload["temperature"] == 0.7
        assert payload["max_tokens"] == 100
        assert payload["top_p"] == 0.9
        assert payload["frequency_penalty"] == 0.5
        assert payload["presence_penalty"] == 0.3
        assert payload["stop"] == ["END"]

    def test_none_params_excluded(self):
        from app.core.proxy import _build_forward_payload

        request = _make_request("Hello")
        payload = _build_forward_payload(request)
        assert "temperature" not in payload
        assert "max_tokens" not in payload


class TestForwardToLLM:
    @pytest.mark.asyncio
    async def test_timeout_raises_502(self):
        from app.core.proxy import _forward_to_llm

        request = _make_request("Hello")
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.side_effect = httpx.TimeoutException("timeout")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            with pytest.raises(LLMProxyError) as exc_info:
                await _forward_to_llm(request, "req-123")
            assert exc_info.value.status_code == 502
            assert "timeout" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_http_error_raises_502(self):
        from app.core.proxy import _forward_to_llm

        request = _make_request("Hello")
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.side_effect = httpx.HTTPError("connection refused")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            with pytest.raises(LLMProxyError) as exc_info:
                await _forward_to_llm(request, "req-123")
            assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_non_200_status_raises_502(self):
        from app.core.proxy import _forward_to_llm

        request = _make_request("Hello")
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = AsyncMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            with pytest.raises(LLMProxyError) as exc_info:
                await _forward_to_llm(request, "req-123")
            assert exc_info.value.status_code == 502
            assert "500" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_invalid_json_raises_502(self):
        from app.core.proxy import _forward_to_llm

        request = _make_request("Hello")
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.json.side_effect = ValueError("invalid json")
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            with pytest.raises(LLMProxyError) as exc_info:
                await _forward_to_llm(request, "req-123")
            assert exc_info.value.status_code == 502
            assert "parse" in exc_info.value.detail.lower()
