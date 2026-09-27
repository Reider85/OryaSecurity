"""Test suite for LLM Security Scanner SDK."""

import pytest
import respx
import httpx
from unittest.mock import Mock

from llm_security_scanner import (
    ScannerClient,
    AsyncScannerClient,
    ScanResult,
    ChatCompletionResponse,
    RuleMatch,
    ScannerError,
    AuthenticationError,
    RateLimitError,
    ScannerAPIError,
)


@pytest.fixture
def mock_scan_response():
    """Mock scan API response."""
    return {
        "verdict": "block",
        "reason": "PII: US SSN detected",
        "latency_ms": 45.2,
        "request_id": "123e4567-e89b-12d3-a456-426614174000",
        "rules_matched": [
            {
                "rule_id": "pii_ssn_us",
                "rule_name": "US Social Security Number",
                "value_hash": "abc123",
                "position": [10, 18],
                "severity": "high",
                "action": "block",
            }
        ],
        "cache_hit": False,
    }


@pytest.fixture
def mock_chat_response():
    """Mock chat completion API response."""
    return {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "created": 1677652288,
        "model": "gpt-3.5-turbo",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hello! How can I help you today?"
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 9,
            "total_tokens": 19
        }
    }


class TestScannerClient:
    """Test synchronous ScannerClient."""
    
    @pytest.fixture
    def client(self):
        """Create a test client."""
        return ScannerClient(
            url="http://localhost:8000",
            api_key="test-api-key",
            timeout=10.0
        )
    
    @respx.mock
    def test_scan_success(self, client, mock_scan_response):
        """Test successful scan operation."""
        # Mock the API response
        respx.post("http://localhost:8000/scan").mock(
            return_value=httpx.Response(
                status_code=200,
                json=mock_scan_response
            )
        )
        
        # Call the scan method
        result = client.scan("What is my SSN? 123-45-6789")
        
        # Verify the result
        assert isinstance(result, ScanResult)
        assert result.verdict == "block"
        assert result.reason == "PII: US SSN detected"
        assert result.latency_ms == 45.2
        assert result.request_id == "123e4567-e89b-12d3-a456-426614174000"
        assert len(result.rules_matched) == 1
        assert result.rules_matched[0].rule_id == "pii_ssn_us"
        assert result.cache_hit is False
    
    @respx.mock
    def test_scan_with_tenant_id(self, client, mock_scan_response):
        """Test scan with tenant ID."""
        respx.post("http://localhost:8000/scan").mock(
            return_value=httpx.Response(
                status_code=200,
                json=mock_scan_response
            )
        )
        
        result = client.scan("Test prompt", tenant_id="tenant-123")
        
        # Verify the request was made with tenant_id
        request = respx.calls.last.request
        assert request.json()["tenant_id"] == "tenant-123"
    
    @respx.mock
    def test_scan_with_metadata(self, client, mock_scan_response):
        """Test scan with metadata."""
        respx.post("http://localhost:8000/scan").mock(
            return_value=httpx.Response(
                status_code=200,
                json=mock_scan_response
            )
        )
        
        metadata = {"source": "web", "user_id": "user-123"}
        result = client.scan("Test prompt", metadata=metadata)
        
        # Verify the request was made with metadata
        request = respx.calls.last.request
        assert request.json()["metadata"] == metadata
    
    @respx.mock
    def test_scan_authentication_error(self, client):
        """Test scan with authentication error."""
        respx.post("http://localhost:8000/scan").mock(
            return_value=httpx.Response(
                status_code=401,
                json={"detail": "Invalid API key"}
            )
        )
        
        with pytest.raises(AuthenticationError) as exc_info:
            client.scan("Test prompt")
        assert "Invalid API key" in str(exc_info.value)
    
    @respx.mock
    def test_scan_rate_limit_error(self, client):
        """Test scan with rate limit error."""
        respx.post("http://localhost:8000/scan").mock(
            return_value=httpx.Response(
                status_code=429,
                headers={"Retry-After": "60"},
                json={"detail": "Rate limit exceeded"}
            )
        )
        
        with pytest.raises(RateLimitError) as exc_info:
            client.scan("Test prompt")
        assert exc_info.value.retry_after == 60
    
    @respx.mock
    def test_scan_api_error(self, client):
        """Test scan with API error."""
        respx.post("http://localhost:8000/scan").mock(
            return_value=httpx.Response(
                status_code=500,
                json={"detail": "Internal server error"}
            )
        )
        
        with pytest.raises(ScannerAPIError) as exc_info:
            client.scan("Test prompt")
        assert exc_info.value.status_code == 500
        assert "Internal server error" in exc_info.value.response_text
    
    @respx.mock
    def test_proxy_chat_success(self, client, mock_chat_response):
        """Test successful proxy chat operation."""
        respx.post("http://localhost:8000/v1/chat/completions").mock(
            return_value=httpx.Response(
                status_code=200,
                json=mock_chat_response
            )
        )
        
        messages = [{"role": "user", "content": "Hello"}]
        result = client.proxy_chat(messages)
        
        assert isinstance(result, ChatCompletionResponse)
        assert result.id == "chatcmpl-123"
        assert result.model == "gpt-3.5-turbo"
        assert len(result.choices) == 1
        assert result.choices[0].message.role == "assistant"
        assert result.choices[0].message.content == "Hello! How can I help you today?"
    
    @respx.mock
    def test_proxy_chat_with_parameters(self, client, mock_chat_response):
        """Test proxy chat with additional parameters."""
        respx.post("http://localhost:8000/v1/chat/completions").mock(
            return_value=httpx.Response(
                status_code=200,
                json=mock_chat_response
            )
        )
        
        messages = [{"role": "user", "content": "Hello"}]
        result = client.proxy_chat(
            messages,
            model="gpt-4",
            temperature=0.7,
            max_tokens=100,
            top_p=0.9,
            frequency_penalty=0.1,
            presence_penalty=0.1,
            stop=["stop"],
            stream=False
        )
        
        # Verify the request was made with all parameters
        request = respx.calls.last.request
        payload = request.json()
        assert payload["model"] == "gpt-4"
        assert payload["temperature"] == 0.7
        assert payload["max_tokens"] == 100
        assert payload["top_p"] == 0.9
        assert payload["frequency_penalty"] == 0.1
        assert payload["presence_penalty"] == 0.1
        assert payload["stop"] == ["stop"]
        assert payload["stream"] is False
    
    @respx.mock
    def test_proxy_chat_authentication_error(self, client):
        """Test proxy chat with authentication error."""
        respx.post("http://localhost:8000/v1/chat/completions").mock(
            return_value=httpx.Response(
                status_code=401,
                json={"detail": "Invalid API key"}
            )
        )
        
        messages = [{"role": "user", "content": "Hello"}]
        
        with pytest.raises(AuthenticationError) as exc_info:
            client.proxy_chat(messages)
        assert "Invalid API key" in str(exc_info.value)
    
    @respx.mock
    def test_proxy_chat_rate_limit_error(self, client):
        """Test proxy chat with rate limit error."""
        respx.post("http://localhost:8000/v1/chat/completions").mock(
            return_value=httpx.Response(
                status_code=429,
                headers={"Retry-After": "30"},
                json={"detail": "Rate limit exceeded"}
            )
        )
        
        messages = [{"role": "user", "content": "Hello"}]
        
        with pytest.raises(RateLimitError) as exc_info:
            client.proxy_chat(messages)
        assert exc_info.value.retry_after == 30
    
    def test_client_context_manager(self):
        """Test client as context manager."""
        with ScannerClient("http://localhost:8000", "test-api-key") as client:
            assert client.api_key == "test-api-key"
        # Session should be closed after context exit


class TestAsyncScannerClient:
    """Test asynchronous AsyncScannerClient."""
    
    @pytest.fixture
    def async_client(self):
        """Create a test async client."""
        return AsyncScannerClient(
            url="http://localhost:8000",
            api_key="test-api-key",
            timeout=10.0
        )
    
    @pytest.mark.asyncio
    @respx.mock
    async def test_async_scan_success(self, async_client, mock_scan_response):
        """Test successful async scan operation."""
        respx.post("http://localhost:8000/scan").mock(
            return_value=httpx.Response(
                status_code=200,
                json=mock_scan_response
            )
        )
        
        result = await async_client.scan("What is my SSN? 123-45-6789")
        
        assert isinstance(result, ScanResult)
        assert result.verdict == "block"
        assert result.reason == "PII: US SSN detected"
        assert result.latency_ms == 45.2
        assert len(result.rules_matched) == 1
    
    @pytest.mark.asyncio
    @respx.mock
    async def test_async_proxy_chat_success(self, async_client, mock_chat_response):
        """Test successful async proxy chat operation."""
        respx.post("http://localhost:8000/v1/chat/completions").mock(
            return_value=httpx.Response(
                status_code=200,
                json=mock_chat_response
            )
        )
        
        messages = [{"role": "user", "content": "Hello"}]
        result = await async_client.proxy_chat(messages)
        
        assert isinstance(result, ChatCompletionResponse)
        assert result.id == "chatcmpl-123"
        assert result.choices[0].message.content == "Hello! How can I help you today?"
    
    @pytest.mark.asyncio
    async def test_async_client_context_manager(self):
        """Test async client as context manager."""
        async with AsyncScannerClient("http://localhost:8000", "test-api-key") as client:
            assert client.api_key == "test-api-key"
        # Client should be closed after context exit


class TestModels:
    """Test Pydantic models."""
    
    def test_scan_result_model(self):
        """Test ScanResult model."""
        rule_match = RuleMatch(
            rule_id="test_rule",
            rule_name="Test Rule",
            value_hash="abc123",
            position=[0, 10],
            severity="high",
            action="block"
        )
        
        result = ScanResult(
            verdict="allow",
            reason="No issues found",
            latency_ms=25.5,
            request_id="123e4567-e89b-12d3-a456-426614174000",
            rules_matched=[rule_match],
            cache_hit=True
        )
        
        assert result.verdict == "allow"
        assert result.reason == "No issues found"
        assert result.latency_ms == 25.5
        assert result.request_id == "123e4567-e89b-12d3-a456-426614174000"
        assert len(result.rules_matched) == 1
        assert result.rules_matched[0].rule_id == "test_rule"
        assert result.cache_hit is True
    
    def test_chat_completion_response_model(self):
        """Test ChatCompletionResponse model."""
        message = ChatCompletionMessage(
            role="assistant",
            content="Hello!"
        )
        
        choice = ChatCompletionChoice(
            index=0,
            message=message,
            finish_reason="stop"
        )
        
        usage = ChatCompletionUsage(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15
        )
        
        response = ChatCompletionResponse(
            id="chatcmpl-123",
            object="chat.completion",
            created=1677652288,
            model="gpt-3.5-turbo",
            choices=[choice],
            usage=usage
        )
        
        assert response.id == "chatcmpl-123"
        assert response.object == "chat.completion"
        assert response.model == "gpt-3.5-turbo"
        assert len(response.choices) == 1
        assert response.choices[0].message.content == "Hello!"
        assert response.usage.total_tokens == 15
    
    def test_rule_match_model(self):
        """Test RuleMatch model."""
        rule_match = RuleMatch(
            rule_id="pii_ssn_us",
            rule_name="US Social Security Number",
            value_hash="abc123",
            position=[10, 18],
            severity="high",
            action="block"
        )
        
        assert rule_match.rule_id == "pii_ssn_us"
        assert rule_match.rule_name == "US Social Security Number"
        assert rule_match.value_hash == "abc123"
        assert rule_match.position == [10, 18]
        assert rule_match.severity == "high"
        assert rule_match.action == "block"


class TestErrorHandling:
    """Test error handling."""
    
    def test_scanner_error(self):
        """Test base ScannerError."""
        with pytest.raises(ScannerError):
            raise ScannerError("Test error")
    
    def test_authentication_error(self):
        """Test AuthenticationError."""
        with pytest.raises(AuthenticationError) as exc_info:
            raise AuthenticationError("Invalid API key")
        assert "Invalid API key" in str(exc_info.value)
    
    def test_rate_limit_error(self):
        """Test RateLimitError."""
        with pytest.raises(RateLimitError) as exc_info:
            raise RateLimitError("Rate limit exceeded", 60)
        assert "Rate limit exceeded" in str(exc_info.value)
        assert exc_info.value.retry_after == 60
    
    def test_scanner_api_error(self):
        """Test ScannerAPIError."""
        with pytest.raises(ScannerAPIError) as exc_info:
            raise ScannerAPIError("API error", 500, "Internal server error")
        assert exc_info.value.status_code == 500
        assert "Internal server error" in exc_info.value.response_text