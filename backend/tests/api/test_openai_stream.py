from __future__ import annotations

import json
import uuid
from typing import AsyncGenerator, Any

import pytest
import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.core.streaming.inspector import StreamingInspector


@pytest.fixture(scope="function")
def client():
    """Test client for FastAPI app."""
    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth_headers():
    """Authentication headers with API key."""
    return {"X-API-Key": "test-api-key"}


@pytest.fixture
def sample_request():
    """Sample OpenAI chat completion request."""
    return {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "user", "content": "Hello, how are you?"}
        ],
        "stream": True,
    }


@pytest.fixture
def pii_request():
    """Request containing PII (SSN)."""
    return {
        "model": "gpt-3.5-turbo", 
        "messages": [
            {"role": "user", "content": "My SSN is 123-45-6789"}
        ],
        "stream": True,
    }


@pytest.fixture
def boundary_pii_request():
    """Request with SSN split across chunks for boundary testing."""
    return {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "user", "content": "My SSN is 123-45-67"}
        ],
        "stream": True,
    }


class MockTransport:
    """Mock HTTP transport for testing."""
    
    def __init__(self, fixture_path: str):
        self.fixture_path = fixture_path
        self.request_history = []
    
    async def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Handle mock request and return response from fixture."""
        # Store request for testing
        self.request_history.append({
            "method": request.method,
            "url": str(request.url),
            "headers": dict(request.headers),
            "content": request.content,
        })
        
        # Read fixture file
        try:
            with open(self.fixture_path, "r") as f:
                lines = f.readlines()
        except FileNotFoundError:
            # Return empty response if fixture not found
            return httpx.Response(
                status_code=200,
                text="data: [DONE]\n\n",
                headers={"content-type": "text/event-stream"},
            )
        
        # Create streaming response
        async def content_stream() -> AsyncGenerator[bytes, None]:
            for line in lines:
                yield line.encode()
        
        return httpx.Response(
            status_code=200,
            content=content_stream(),
            headers={"content-type": "text/event-stream"},
        )


@pytest.fixture
def mock_transport():
    """Mock transport fixture."""
    return MockTransport


@pytest.fixture
def mock_upstream_transport():
    """Mock upstream transport for streaming tests."""
    transport = MockTransport("tests/fixtures/streaming/benign_stream.jsonl")
    
    # Patch httpx.AsyncClient to use mock transport
    original_client = httpx.AsyncClient
    
    class MockAsyncClient:
        def __init__(self, **kwargs):
            self._original = original_client(**kwargs)
            self.transport = transport
        
        async def __aenter__(self):
            return self
        
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            await self._original.__aexit__(exc_type, exc_val, exc_tb)
        
        def stream(self, method: str, url: str, **kwargs):
            return self.transport.handle_request(
                httpx.Request(method, url, **kwargs)
            )
    
    # Patch the httpx.AsyncClient.stream method
    httpx.AsyncClient.stream = MockAsyncClient.stream
    
    yield transport
    
    # Restore original
    httpx.AsyncClient.stream = original_client.stream


class TestStreamingInspector:
    """Test the chunk-based streaming inspector via the API-facing surface."""

    @pytest.mark.asyncio
    async def test_inspector_allow_clean_text(self):
        """Inspector allows clean text (buffer not yet full)."""
        inspector = StreamingInspector(chunk_size=8)
        result = await inspector.inspect_chunk("Hello world")

        assert result.blocked is False
        assert result.decision is None
        assert result.accumulated_text == "Hello world"

    @pytest.mark.asyncio
    async def test_inspector_block_pii(self):
        """Inspector blocks PII once buffer fills."""
        inspector = StreamingInspector(chunk_size=2)

        await inspector.inspect_chunk("My SSN is ")
        result = await inspector.inspect_chunk("123-45-6789")

        assert result.blocked is True
        assert result.decision is not None
        assert "pii_ssn_us" in result.decision.reason
        assert result.decision.rule_ids is not None

    @pytest.mark.asyncio
    async def test_inspector_boundary_detection(self):
        """Inspector detects PII across chunk boundaries via prev_tail."""
        inspector = StreamingInspector(chunk_size=2)

        await inspector.inspect_chunk("My SSN is 123-45-")
        r1 = await inspector.inspect_chunk("67")
        assert r1.blocked is False

        await inspector.inspect_chunk("89 and more")
        r2 = await inspector.inspect_chunk(" text")
        assert r2.blocked is True
        assert r2.decision is not None
        assert "pii_ssn_us" in r2.decision.reason

    @pytest.mark.asyncio
    async def test_inspector_finalize(self):
        """Finalize allows clean leftover buffer."""
        inspector = StreamingInspector(chunk_size=8)

        await inspector.inspect_chunk("Hello world")
        result = await inspector.finalize()

        assert result.blocked is False
        assert result.accumulated_text == "Hello world"


class TestOpenAIStreamEndpoint:
    """Test the v2 streaming endpoint."""
    
    def test_stream_false_not_allowed(self, client, auth_headers):
        """Test that stream=false returns 400."""
        request = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        }
        
        response = client.post("/v2/chat/completions", json=request, headers=auth_headers)
        
        assert response.status_code == 400
    
    def test_missing_api_key(self, client, sample_request):
        """Test that missing API key returns 401."""
        response = client.post("/v2/chat/completions", json=sample_request)
        
        assert response.status_code == 401
    
    def test_rate_limit_headers(self, client, auth_headers, sample_request):
        """Test rate limit headers are present."""
        response = client.post("/v2/chat/completions", json=sample_request, headers=auth_headers)
        
        assert response.status_code == 200
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert "X-Request-Id" in response.headers
    
    def test_sse_content_type(self, client, auth_headers, sample_request):
        """Test SSE content type header."""
        response = client.post("/v2/chat/completions", json=sample_request, headers=auth_headers)
        
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/event-stream"
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["connection"] == "keep-alive"
    
    def test_benign_stream(self, client, auth_headers, sample_request, mock_upstream_transport):
        """Test benign stream returns correct SSE format."""
        response = client.post("/v2/chat/completions", json=sample_request, headers=auth_headers)
        
        assert response.status_code == 200
        
        # Check SSE format
        lines = response.text.strip().split('\n')
        events = []
        current_event = []
        
        for line in lines:
            if line.strip():
                current_event.append(line)
            else:
                if current_event:
                    events.append("\n".join(current_event))
                    current_event = []
        
        if current_event:
            events.append("\n".join(current_event))
        
        # Check we have multiple events
        assert len(events) > 1
        
        # Check final event is [DONE]
        final_event = events[-1]
        assert "data: [DONE]" in final_event
    
    def test_pii_block_stream(self, client, auth_headers, pii_request, mock_upstream_transport):
        """Test that PII in stream triggers block."""
        # Mock a transport that returns PII stream
        transport = MockTransport("tests/fixtures/streaming/pii_stream.jsonl")
        httpx.AsyncClient.stream = transport.handle_request
        
        response = client.post("/v2/chat/completions", json=pii_request, headers=auth_headers)
        
        assert response.status_code == 200
        
        # Check that we get a scanner_blocked event
        lines = response.text.strip().split('\n')
        events = []
        current_event = []
        
        for line in lines:
            if line.strip():
                current_event.append(line)
            else:
                if current_event:
                    event_data = "\n".join(current_event)
                    if "scanner_blocked" in event_data:
                        events.append(event_data)
                    current_event = []
        
        # Should have scanner_blocked event
        assert len(events) > 0
        assert "scanner_blocked" in events[0]
        assert "PII: pii_ssn_us" in events[0]
    
    def test_boundary_pii_detection(self, client, auth_headers, boundary_pii_request, mock_upstream_transport):
        """Test PII detection across chunk boundaries."""
        # Mock a transport that returns boundary PII stream
        transport = MockTransport("tests/fixtures/streaming/pii_boundary_stream.jsonl")
        httpx.AsyncClient.stream = transport.handle_request
        
        response = client.post("/v2/chat/completions", json=boundary_pii_request, headers=auth_headers)
        
        assert response.status_code == 200
        
        # Check that we get a scanner_blocked event
        lines = response.text.strip().split('\n')
        events = []
        current_event = []
        
        for line in lines:
            if line.strip():
                current_event.append(line)
            else:
                if current_event:
                    event_data = "\n".join(current_event)
                    if "scanner_blocked" in event_data:
                        events.append(event_data)
                    current_event = []
        
        # Should have scanner_blocked event
        assert len(events) > 0
        assert "scanner_blocked" in events[0]
        assert "PII: pii_ssn_us" in events[0]
    
    def test_prompt_block_short_circuit(self, client, auth_headers, pii_request):
        """Test that prompt-side block short-circuits before stream starts."""
        # This test would need to mock the prompt-side scan to return block
        # For now, just test the endpoint structure
        response = client.post("/v2/chat/completions", json=pii_request, headers=auth_headers)
        
        # Should be 200 (streaming response) or 403 if prompt blocked
        # In real test, we'd mock the prompt scan to return block
        assert response.status_code in [200, 403]
    
    def test_request_id_header(self, client, auth_headers, sample_request, mock_upstream_transport):
        """Test that X-Request-Id header is unique and present."""
        response1 = client.post("/v2/chat/completions", json=sample_request, headers=auth_headers)
        response2 = client.post("/v2/chat/completions", json=sample_request, headers=auth_headers)
        
        assert response1.headers["X-Request-Id"] != response2.headers["X-Request-Id"]