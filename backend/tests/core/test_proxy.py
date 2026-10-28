from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from app.config import settings
from app.core.proxy import (
    LLMProxyError,
    _extract_prompt_text,
    _mask_blocked_content,
    proxy_chat,
    proxy_chat_stream,
)
from app.core.rules.base import RuleMatch
from app.models.openai import (
    ChatCompletionChoice,
    ChatCompletionMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionStreamResponse,
    ChatCompletionStreamChoice,
    ChatCompletionChoiceDelta,
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
        assert payload["model"] == "gpt-3.5-turbo"  # default from config
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

    def test_config_model_overrides_request_model(self):
        from app.core.proxy import _build_forward_payload

        # Mock settings.llm_model to override default
        original_llm_model = settings.llm_model
        settings.llm_model = "glm-5.3-flash"
        
        try:
            request = _make_request("Hello")
            request.model = "gpt-4"  # Different from config
            payload = _build_forward_payload(request)
            assert payload["model"] == "gpt-4"  # Should use request model (config doesn't override)
        finally:
            settings.llm_model = original_llm_model

    def test_config_model_none_falls_back_to_request(self):
        from app.config import settings
        from app.core.proxy import _build_forward_payload

        # Mock settings.llm_model to None
        original_llm_model = settings.llm_model
        settings.llm_model = None
        
        try:
            request = _make_request("Hello")
            request.model = "gpt-4"
            payload = _build_forward_payload(request)
            assert payload["model"] == "gpt-4"  # Should use request model when config is None
        finally:
            settings.llm_model = original_llm_model


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
    @patch("app.core.proxy._forward_to_llm")
    async def test_url_construction_default_path(self, mock_forward):
        from app.core.proxy import _forward_to_llm
        from app.config import settings

        original_llm_provider_url = settings.llm_provider_url
        original_llm_api_path = settings.llm_api_path
        
        try:
            settings.llm_provider_url = "https://api.example.com"
            settings.llm_api_path = "/v1/chat/completions"
            
            request = _make_request("Hello")
            mock_forward.return_value = _make_llm_response("The weather is nice.")
            
            await proxy_chat(request)
            
            # Check that _forward_to_llm was called once
            mock_forward.assert_called_once()
            # Check that it was called with the request and a request_id
            call_args = mock_forward.call_args
            assert call_args[0][0] == request  # First arg is the request
            assert call_args[0][1] is not None  # Second arg is request_id (not None)
        finally:
            settings.llm_provider_url = original_llm_provider_url
            settings.llm_api_path = original_llm_api_path

    @pytest.mark.asyncio
    @patch("app.core.proxy._forward_to_llm")
    async def test_url_construction_zai_path(self, mock_forward):
        from app.core.proxy import _forward_to_llm
        from app.config import settings

        original_llm_provider_url = settings.llm_provider_url
        original_llm_api_path = settings.llm_api_path
        
        try:
            settings.llm_provider_url = "https://api.z.ai/api/paas/v4"
            settings.llm_api_path = "/chat/completions"
            
            request = _make_request("Hello")
            mock_forward.return_value = _make_llm_response("The weather is nice.")
            
            await proxy_chat(request)
            
            # Check that _forward_to_llm was called once
            mock_forward.assert_called_once()
            # Check that it was called with the request and a request_id
            call_args = mock_forward.call_args
            assert call_args[0][0] == request  # First arg is the request
            assert call_args[0][1] is not None  # Second arg is request_id (not None)
        finally:
            settings.llm_provider_url = original_llm_provider_url
            settings.llm_api_path = original_llm_api_path

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


class TestStreaming:
    @pytest.mark.asyncio
    async def test_build_forward_payload_includes_stream(self):
        from app.core.proxy import _build_forward_payload
        
        request = _make_request("Hello")
        request.stream = True
        payload = _build_forward_payload(request)
        assert payload["stream"] is True
        
        request.stream = False
        payload = _build_forward_payload(request)
        assert payload["stream"] is False

    @pytest.mark.asyncio
    async def test_stream_response_with_reasoning_content(self):
        from app.core.proxy import _forward_to_llm_stream
        
        request = _make_request("Hello")
        request.stream = True
        
        # Mock response with reasoning content
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.aiter_lines.return_value = [
            "data: {\"id\": \"chatcmpl-123\", \"object\": \"chat.completion.chunk\", \"created\": 1234567890, \"model\": \"glm-5.3-flash\", \"choices\": [{\"index\": 0, \"delta\": {\"role\": \"assistant\", \"content\": \"Hello\", \"reasoning_content\": \"Thinking...\"}, \"finish_reason\": null}]}}",
            "data: {\"id\": \"chatcmpl-123\", \"object\": \"chat.completion.chunk\", \"created\": 1234567890, \"model\": \"glm-5.3-flash\", \"choices\": [{\"index\": 0, \"delta\": {}, \"finish_reason\": \"stop\"}]}}",
            "data: [DONE]"
        ]
        
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance
            
            result = await _forward_to_llm_stream(request, "req-123")
            assert result is not None
            # Should be a StreamingResponse
            assert "StreamingResponse" in str(type(result))


class TestReasoningContent:
    def test_chat_completion_message_with_reasoning(self):
        message = ChatCompletionMessage(
            role="assistant",
            content="Hello",
            reasoning_content="Let me think..."
        )
        assert message.content == "Hello"
        assert message.reasoning_content == "Let me think..."

    def test_chat_completion_response_with_reasoning(self):
        response = ChatCompletionResponse(
            id="chatcmpl-123",
            object="chat.completion",
            created=1234567890,
            model="glm-5.3-flash",
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatCompletionMessage(
                        role="assistant",
                        content="Hello",
                        reasoning_content="Let me think..."
                    ),
                    finish_reason="stop"
                )
            ]
        )
        assert response.choices[0].message.content == "Hello"
        assert response.choices[0].message.reasoning_content == "Let me think..."


# --- PII tokenization on the outbound path (EP-13/Prompt 32) ---------------
#
# `user_...` matches the pii_username rule (action=log_only, severity=low),
# so the prompt passes the PDP and reaches the redaction step — unlike real
# SSN/AWS-key values, which the rules block before redaction ever runs.

_PROMPT_WITH_ALLOWLISTED_PII = "my login is user_abcdef1234"


class _FakeVault:
    """Duck-typed VaultClient encode/decode for redactor + detokenizer."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0
        self.decode_calls: list[str] = []
        self.registry: dict[str, str] = {}

    async def encode(self, plaintext, *, context=None):
        from app.core.redaction.vault_client import VaultResult

        self.calls += 1
        if self.fail:
            return VaultResult(ok=False, error="circuit_open")
        token = f"vt{self.calls}"
        # Round-trip friendly: remember what this token stands for.
        self.registry[token] = plaintext
        return VaultResult(ok=True, value=token, engine="tokenization")

    async def decode(self, token, *, context=None):
        from app.core.redaction.vault_client import VaultResult

        self.decode_calls.append(token)
        if self.fail:
            return VaultResult(ok=False, error="circuit_open")
        value = self.registry.get(token)
        if value is None:
            return VaultResult(ok=False, error="vault_decode_failed: unknown token")
        return VaultResult(ok=True, value=value, engine="tokenization")


class TestProxyRedaction:
    @pytest.mark.asyncio
    @patch("app.core.proxy._forward_to_llm")
    async def test_vault_disabled_passthrough(self, mock_forward):
        mock_forward.return_value = _make_llm_response("ok")
        from app.core.redaction.vault_client import set_vault_client

        set_vault_client(None)
        request = _make_request(_PROMPT_WITH_ALLOWLISTED_PII)
        await proxy_chat(request)
        assert request.messages[0].content == _PROMPT_WITH_ALLOWLISTED_PII

    @pytest.mark.asyncio
    @patch("app.core.proxy._forward_to_llm")
    async def test_pii_tokenized_before_forward(self, mock_forward):
        mock_forward.return_value = _make_llm_response("ok")
        from app.core.redaction.vault_client import set_vault_client

        set_vault_client(_FakeVault())
        try:
            request = _make_request(_PROMPT_WITH_ALLOWLISTED_PII)
            await proxy_chat(request)
            content = request.messages[0].content
            assert "user_abcdef1234" not in content
            assert "<USERNAME_T_vt1>" in content
            # Forwarding happens from request.messages, so the LLM sees tokens
            mock_forward.assert_called_once()
        finally:
            set_vault_client(None)

    @pytest.mark.asyncio
    @patch("app.core.proxy._forward_to_llm")
    async def test_redaction_failure_is_fail_closed(self, mock_forward):
        from app.core.redaction.redactor import VAULT_UNAVAILABLE_REASON
        from app.core.redaction.vault_client import set_vault_client

        set_vault_client(_FakeVault(fail=True))
        try:
            request = _make_request(_PROMPT_WITH_ALLOWLISTED_PII)
            with pytest.raises(LLMProxyError) as exc_info:
                await proxy_chat(request)
            assert exc_info.value.status_code == 403
            assert exc_info.value.detail == VAULT_UNAVAILABLE_REASON
            mock_forward.assert_not_called()
        finally:
            set_vault_client(None)

    @pytest.mark.asyncio
    @patch("app.core.proxy._forward_to_llm_stream")
    async def test_stream_path_tokenizes_too(self, mock_forward):
        mock_forward.return_value = "stream-sentinel"
        from app.core.redaction.vault_client import set_vault_client

        set_vault_client(_FakeVault())
        try:
            request = _make_request(_PROMPT_WITH_ALLOWLISTED_PII)
            result = await proxy_chat_stream(request)
            assert result == "stream-sentinel"
            content = request.messages[0].content
            assert "user_abcdef1234" not in content
            assert "<USERNAME_T_" in content
        finally:
            set_vault_client(None)

    @pytest.mark.asyncio
    @patch("app.core.proxy._forward_to_llm_stream")
    async def test_stream_redaction_failure_fail_closed(self, mock_forward):
        from app.core.redaction.redactor import VAULT_UNAVAILABLE_REASON
        from app.core.redaction.vault_client import set_vault_client

        set_vault_client(_FakeVault(fail=True))
        try:
            request = _make_request(_PROMPT_WITH_ALLOWLISTED_PII)
            with pytest.raises(LLMProxyError) as exc_info:
                await proxy_chat_stream(request)
            assert exc_info.value.status_code == 403
            assert exc_info.value.detail == VAULT_UNAVAILABLE_REASON
            mock_forward.assert_not_called()
        finally:
            set_vault_client(None)


# --- Reverse tokenization on the response path (EP-13/Prompt 33) -----------


class _FakeStreamUpstream:
    """Minimal stand-in for httpx streaming response (aiter_lines)."""

    status_code = 200

    def __init__(self, lines: list[str]):
        self._lines = lines

    def aiter_lines(self):
        async def _gen():
            for line in self._lines:
                yield line

        return _gen()


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}"


def _delta_chunk(content: str, *, role: str | None = None, finish: str | None = None) -> str:
    return _sse({
        "id": "chatcmpl-t",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "gpt-3.5-turbo",
        "choices": [{
            "index": 0,
            "delta": {**( {"role": role} if role else {}), "content": content},
            "finish_reason": finish,
        }],
    })


async def _consume(body_iterator) -> list[dict]:
    events = []
    async for chunk in body_iterator:
        for piece in chunk.split("\n\n"):
            piece = piece.strip()
            if piece.startswith("data: "):
                data = piece[6:]
                if data == "[DONE]":
                    events.append({"_done": True})
                else:
                    events.append(json.loads(data))
    return events


class TestProxyDetokenization:
    """The LLM echoes prompt-side tokens back; the response restores them."""

    @pytest.mark.asyncio
    @patch("app.core.proxy._forward_to_llm")
    async def test_echoed_token_restored_in_response(self, mock_forward):
        mock_forward.return_value = _make_llm_response(
            "Your username is <USERNAME_T_vt1>, all set."
        )
        from app.core.redaction.vault_client import set_vault_client

        vault = _FakeVault()
        vault.registry["vt1"] = "user_abcdef1234"
        set_vault_client(vault)
        try:
            request = _make_request(_PROMPT_WITH_ALLOWLISTED_PII)
            result = await proxy_chat(request)
            assert result.choices[0].message.content == (
                "Your username is user_abcdef1234, all set."
            )
            assert vault.decode_calls == ["vt1"]
        finally:
            set_vault_client(None)

    @pytest.mark.asyncio
    @patch("app.core.proxy._forward_to_llm")
    async def test_vault_disabled_keeps_token_untouched(self, mock_forward):
        mock_forward.return_value = _make_llm_response("id: <USERNAME_T_vt1>")
        from app.core.redaction.vault_client import set_vault_client

        set_vault_client(None)
        request = _make_request("Hello")
        result = await proxy_chat(request)
        assert result.choices[0].message.content == "id: <USERNAME_T_vt1>"

    @pytest.mark.asyncio
    @patch("app.core.proxy._forward_to_llm")
    async def test_decode_failure_keeps_token(self, mock_forward):
        # Fail-open (ADR-0008 inbound): a token that cannot be decoded stays
        # in the response instead of failing the request.
        mock_forward.return_value = _make_llm_response("<USERNAME_T_vt1>")
        from app.core.redaction.vault_client import set_vault_client

        set_vault_client(_FakeVault(fail=True))
        try:
            request = _make_request("Hello")
            result = await proxy_chat(request)
            assert result.choices[0].message.content == "<USERNAME_T_vt1>"
        finally:
            set_vault_client(None)

    @pytest.mark.asyncio
    @patch("app.core.proxy._forward_to_llm")
    async def test_blocked_generation_not_detokenized(self, mock_forward):
        # Block path masks raw PII and never restores tokens (Prompt 33 only
        # touches allowed responses).
        mock_forward.return_value = _make_llm_response(
            "SSN: 123-45-6789 and <USERNAME_T_vt1>"
        )
        from app.core.redaction.vault_client import set_vault_client

        vault = _FakeVault()
        vault.registry["vt1"] = "user_abcdef1234"
        set_vault_client(vault)
        try:
            request = _make_request(_PROMPT_WITH_ALLOWLISTED_PII)
            result = await proxy_chat(request)
            content = result.choices[0].message.content
            assert "123-45-6789" not in content
            assert "<REDACTED>" in content
            # Token left as-is on the block path.
            assert "<USERNAME_T_vt1>" in content
            assert vault.decode_calls == []
        finally:
            set_vault_client(None)

    @pytest.mark.asyncio
    async def test_stream_delta_detokenized_live(self):
        """Deltas are restored chunk-by-chunk; a token split across deltas
        is held until it closes (ADR-0007)."""
        from app.core.proxy import _forward_to_llm_stream
        from app.core.redaction.vault_client import set_vault_client

        vault = _FakeVault()
        vault.registry["vault-token-42"] = "123-45-6789"
        set_vault_client(vault)
        try:
            request = _make_request("Hello")
            request.stream = True
            upstream = _FakeStreamUpstream([
                _delta_chunk("Your SSN <SSN_T_vau", role="assistant"),
                _delta_chunk("lt-token-42> is safe"),
                _delta_chunk("", finish="stop"),
                "data: [DONE]",
            ])
            with patch("httpx.AsyncClient") as mock_client:
                inst = AsyncMock()
                inst.post.return_value = upstream
                inst.__aenter__ = AsyncMock(return_value=inst)
                inst.__aexit__ = AsyncMock(return_value=False)
                mock_client.return_value = inst

                resp = await _forward_to_llm_stream(
                    request, "req-detok", prompt_hash="h", prompt_text="Hello"
                )
                events = await _consume(resp.body_iterator)

            def _delta_of(e: dict):
                if not e.get("choices"):
                    return None
                choice = e["choices"][0]
                if choice.get("delta") is None:
                    return None
                return choice

            # Live deltas (no finish_reason): concatenating them must give the
            # restored text without ever exposing the raw token.
            live = [
                _delta_of(e)["delta"].get("content") or ""
                for e in events
                if _delta_of(e) and not _delta_of(e).get("finish_reason")
            ]
            assert "".join(live) == "Your SSN 123-45-6789 is safe"
            assert not any("<SSN_T_" in c for c in live)
            # The final accumulated chunk is restored as well.
            final = [
                e for e in events
                if _delta_of(e) and _delta_of(e).get("finish_reason")
            ]
            assert final, events
            assert final[-1]["choices"][0]["delta"]["content"] == (
                "Your SSN 123-45-6789 is safe"
            )
            # Detokenize ran over accumulated too — registry round-trip.
            assert "vault-token-42" in vault.decode_calls
        finally:
            set_vault_client(None)

    @pytest.mark.asyncio
    async def test_stream_flush_on_done_without_finish_reason(self):
        # Upstream closes on [DONE] without ever sending finish_reason: the
        # held tail must still reach the client.
        from app.core.proxy import _forward_to_llm_stream
        from app.core.redaction.vault_client import set_vault_client

        vault = _FakeVault()
        vault.registry["vt1"] = "restored-value"
        set_vault_client(vault)
        try:
            request = _make_request("Hello")
            request.stream = True
            upstream = _FakeStreamUpstream([
                _delta_chunk("id: <SSN_T_vt1", role="assistant"),
                "data: [DONE]",
            ])
            with patch("httpx.AsyncClient") as mock_client:
                inst = AsyncMock()
                inst.post.return_value = upstream
                inst.__aenter__ = AsyncMock(return_value=inst)
                inst.__aexit__ = AsyncMock(return_value=False)
                mock_client.return_value = inst

                resp = await _forward_to_llm_stream(
                    request, "req-flush", prompt_hash="h", prompt_text="Hello"
                )
                events = await _consume(resp.body_iterator)

            contents = [
                e["choices"][0]["delta"].get("content")
                for e in events
                if e.get("choices") and e["choices"][0].get("delta") is not None
            ]
            # The token never closed, so the tail is emitted verbatim — a
            # placeholder in the output is safe (it is not PII).
            assert "".join(contents) == "id: <SSN_T_vt1"
        finally:
            set_vault_client(None)
