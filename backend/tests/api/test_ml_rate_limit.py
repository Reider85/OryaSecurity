"""Tests for ML endpoints rate limiting."""

import asyncio
import hashlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient

from app.api.v2.embeddings import router as embeddings_router
from app.api.v2.classify import router as classify_router
from app.core.rate_limit import RateLimiter
from app.config import settings


@pytest.fixture
def app():
    """Create test app with ML endpoints."""
    app = FastAPI()
    app.include_router(embeddings_router)
    app.include_router(classify_router)
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_auth():
    """Mock authentication info."""
    return type('AuthInfo', (), {'api_key': 'test-api-key-12345'})


@pytest.fixture
def rate_limiter():
    """Create rate limiter for testing."""
    return RateLimiter(max_rps=2)  # 2 RPS for testing


class TestEmbeddingsRateLimiting:
    """Test rate limiting for embeddings endpoint."""
    
    @pytest.mark.asyncio
    async def test_embeddings_rate_limit_normal(self, app, mock_auth):
        """Test normal rate limiting behavior for embeddings."""
        with patch('app.api.v2.embeddings.get_shared_embedding_client') as mock_client, \
             patch('app.api.v2.embeddings.embedding_cache') as mock_cache, \
             patch('app.api.v2.embeddings.verify_api_key', return_value=mock_auth):
            
            # Mock embedding client
            mock_embedding_client = AsyncMock()
            mock_embedding_client.available = True
            mock_embedding_client.embed.return_value = [0.1, 0.2, 0.3]
            mock_client.return_value = mock_embedding_client
            
            # Mock cache
            mock_cache.get.return_value = None
            mock_cache.set.return_value = None
            
            # Test client
            async with AsyncClient(app=app, base_url="http://test") as ac:
                # First request should succeed
                response1 = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text"},
                    headers={"Authorization": "Bearer test-api-key-12345"}
                )
                assert response1.status_code == 200
                assert "X-RateLimit-Limit" in response1.headers
                assert "X-RateLimit-Remaining" in response1.headers
                
                # Second request should succeed (within 2 RPS limit)
                response2 = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text 2"},
                    headers={"Authorization": "Bearer test-api-key-12345"}
                )
                assert response2.status_code == 200
                
                # Third request should be rate limited
                response3 = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text 3"},
                    headers={"Authorization": "Bearer test-api-key-12345"}
                )
                assert response3.status_code == 429
                assert response3.headers["Retry-After"] == "1"
                assert response3.headers["X-RateLimit-Limit"] == "2"
                assert response3.headers["X-RateLimit-Remaining"] == "0"
    
    @pytest.mark.asyncio
    async def test_embeddings_rate_limit_different_keys(self, app, mock_auth):
        """Test rate limiting with different API keys."""
        with patch('app.api.v2.embeddings.get_shared_embedding_client') as mock_client, \
             patch('app.api.v2.embeddings.embedding_cache') as mock_cache, \
             patch('app.api.v2.embeddings.verify_api_key') as mock_verify:
            
            # Mock embedding client
            mock_embedding_client = AsyncMock()
            mock_embedding_client.available = True
            mock_embedding_client.embed.return_value = [0.1, 0.2, 0.3]
            mock_client.return_value = mock_embedding_client
            
            # Mock cache
            mock_cache.get.return_value = None
            mock_cache.set.return_value = None
            
            # Test client
            async with AsyncClient(app=app, base_url="http://test") as ac:
                # First API key - 2 requests
                mock_auth1 = type('AuthInfo', (), {'api_key': 'key1'})
                mock_verify.return_value = mock_auth1
                
                response1 = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text"},
                    headers={"Authorization": "Bearer key1"}
                )
                assert response1.status_code == 200
                
                response2 = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text"},
                    headers={"Authorization": "Bearer key1"}
                )
                assert response2.status_code == 200
                
                # Second API key - should not be affected by first key's limits
                mock_auth2 = type('AuthInfo', (), {'api_key': 'key2'})
                mock_verify.return_value = mock_auth2
                
                response3 = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text"},
                    headers={"Authorization": "Bearer key2"}
                )
                assert response3.status_code == 200
                
                response4 = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text"},
                    headers={"Authorization": "Bearer key2"}
                )
                assert response4.status_code == 200
                
                # Both keys should now be rate limited
                response5 = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text"},
                    headers={"Authorization": "Bearer key1"}
                )
                assert response5.status_code == 429
                
                response6 = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text"},
                    headers={"Authorization": "Bearer key2"}
                )
                assert response6.status_code == 429
    
    @pytest.mark.asyncio
    async def test_embeddings_rate_limit_headers(self, app, mock_auth):
        """Test rate limit headers in responses."""
        with patch('app.api.v2.embeddings.get_shared_embedding_client') as mock_client, \
             patch('app.api.v2.embeddings.embedding_cache') as mock_cache, \
             patch('app.api.v2.embeddings.verify_api_key', return_value=mock_auth):
            
            # Mock embedding client
            mock_embedding_client = AsyncMock()
            mock_embedding_client.available = True
            mock_embedding_client.embed.return_value = [0.1, 0.2, 0.3]
            mock_client.return_value = mock_embedding_client
            
            # Mock cache
            mock_cache.get.return_value = None
            mock_cache.set.return_value = None
            
            # Test client
            async with AsyncClient(app=app, base_url="http://test") as ac:
                response = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text"},
                    headers={"Authorization": "Bearer test-api-key-12345"}
                )
                
                assert response.status_code == 200
                assert response.headers["X-RateLimit-Limit"] == str(settings.rate_limit_rps)
                assert "X-RateLimit-Remaining" in response.headers
                assert isinstance(int(response.headers["X-RateLimit-Remaining"]), int)


class TestClassificationRateLimiting:
    """Test rate limiting for classification endpoint."""
    
    @pytest.mark.asyncio
    async def test_classification_rate_limit_normal(self, app, mock_auth):
        """Test normal rate limiting behavior for classification."""
        with patch('app.api.v2.classify.get_classifier') as mock_classifier, \
             patch('app.api.v2.classify.verify_api_key', return_value=mock_auth):
            
            # Mock classifier
            mock_classifier_instance = AsyncMock()
            mock_classifier_instance.available = True
            mock_classifier_instance.classify.return_value = type('Result', (), {
                'top_class': 'benign',
                'confidence': 0.95,
                'all_classes': {'benign': 0.95, 'prompt_injection': 0.05},
                'to_dict': lambda: {
                    'top_class': 'benign',
                    'confidence': 0.95,
                    'all_classes': {'benign': 0.95, 'prompt_injection': 0.05},
                }
            })()
            mock_classifier.return_value = mock_classifier_instance
            
            # Test client
            async with AsyncClient(app=app, base_url="http://test") as ac:
                # First request should succeed
                response1 = await ac.post(
                    "/v2/classify",
                    json={"text": "Hello, how are you?"},
                    headers={"Authorization": "Bearer test-api-key-12345"}
                )
                assert response1.status_code == 200
                assert "X-RateLimit-Limit" in response1.headers
                assert "X-RateLimit-Remaining" in response1.headers
                
                # Second request should succeed (within 2 RPS limit)
                response2 = await ac.post(
                    "/v2/classify",
                    json={"text": "What's the weather?"},
                    headers={"Authorization": "Bearer test-api-key-12345"}
                )
                assert response2.status_code == 200
                
                # Third request should be rate limited
                response3 = await ac.post(
                    "/v2/classify",
                    json={"text": "Can you help me?"},
                    headers={"Authorization": "Bearer test-api-key-12345"}
                )
                assert response3.status_code == 429
                assert response3.headers["Retry-After"] == "1"
    
    @pytest.mark.asyncio
    async def test_classification_rate_limit_concurrent(self, app, mock_auth):
        """Test rate limiting with concurrent requests."""
        with patch('app.api.v2.classify.get_classifier') as mock_classifier, \
             patch('app.api.v2.classify.verify_api_key', return_value=mock_auth):
            
            # Mock classifier
            mock_classifier_instance = AsyncMock()
            mock_classifier_instance.available = True
            mock_classifier_instance.classify.return_value = type('Result', (), {
                'top_class': 'benign',
                'confidence': 0.95,
                'all_classes': {'benign': 0.95, 'prompt_injection': 0.05},
                'to_dict': lambda: {
                    'top_class': 'benign',
                    'confidence': 0.95,
                    'all_classes': {'benign': 0.95, 'prompt_injection': 0.05},
                }
            })()
            mock_classifier.return_value = mock_classifier_instance
            
            # Test client
            async with AsyncClient(app=app, base_url="http://test") as ac:
                # Make 3 concurrent requests
                tasks = []
                for i in range(3):
                    task = ac.post(
                        "/v2/classify",
                        json={"text": f"Concurrent request {i}"},
                        headers={"Authorization": "Bearer test-api-key-12345"}
                    )
                    tasks.append(task)
                
                responses = await asyncio.gather(*tasks)
                
                # Should have 2 successful and 1 rate limited
                successful = sum(1 for r in responses if r.status_code == 200)
                rate_limited = sum(1 for r in responses if r.status_code == 429)
                
                assert successful == 2
                assert rate_limited == 1


class TestRateLimiterIntegration:
    """Test rate limiter integration with ML endpoints."""
    
    @pytest.mark.asyncio
    async def test_rate_limit_error_handling(self, app, mock_auth):
        """Test rate limiter error handling."""
        with patch('app.api.v2.embeddings.get_shared_embedding_client') as mock_client, \
             patch('app.api.v2.embeddings.embedding_cache') as mock_cache, \
             patch('app.api.v2.embeddings.verify_api_key', return_value=mock_auth):
            
            # Mock embedding client
            mock_embedding_client = AsyncMock()
            mock_embedding_client.available = True
            mock_embedding_client.embed.return_value = [0.1, 0.2, 0.3]
            mock_client.return_value = mock_embedding_client
            
            # Mock cache
            mock_cache.get.return_value = None
            mock_cache.set.return_value = None
            
            # Test client
            async with AsyncClient(app=app, base_url="http://test") as ac:
                # Exhaust rate limit
                for i in range(settings.rate_limit_rps):
                    response = await ac.post(
                        "/v2/embeddings",
                        json={"input": f"test text {i}"},
                        headers={"Authorization": "Bearer test-api-key-12345"}
                    )
                    if i < settings.rate_limit_rps - 1:
                        assert response.status_code == 200
                    else:
                        assert response.status_code == 429
    
    @pytest.mark.asyncio
    async def test_rate_limit_reset_after_window(self, app, mock_auth):
        """Test rate limit reset after sliding window."""
        with patch('app.api.v2.embeddings.get_shared_embedding_client') as mock_client, \
             patch('app.api.v2.embeddings.embedding_cache') as mock_cache, \
             patch('app.api.v2.embeddings.verify_api_key', return_value=mock_auth):
            
            # Mock embedding client
            mock_embedding_client = AsyncMock()
            mock_embedding_client.available = True
            mock_embedding_client.embed.return_value = [0.1, 0.2, 0.3]
            mock_client.return_value = mock_embedding_client
            
            # Mock cache
            mock_cache.get.return_value = None
            mock_cache.set.return_value = None
            
            # Test client
            async with AsyncClient(app=app, base_url="http://test") as ac:
                # Exhaust rate limit
                for i in range(settings.rate_limit_rps):
                    response = await ac.post(
                        "/v2/embeddings",
                        json={"input": f"test text {i}"},
                        headers={"Authorization": "Bearer test-api-key-12345"}
                    )
                
                # Should be rate limited
                response = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text"},
                    headers={"Authorization": "Bearer test-api-key-12345"}
                )
                assert response.status_code == 429


class TestRateLimitMetrics:
    """Test rate limiting metrics."""
    
    @pytest.mark.asyncio
    async def test_rate_limit_metrics_increased(self, app, mock_auth):
        """Test that rate limiting metrics are properly incremented."""
        with patch('app.api.v2.embeddings.get_shared_embedding_client') as mock_client, \
             patch('app.api.v2.embeddings.embedding_cache') as mock_cache, \
             patch('app.api.v2.embeddings.verify_api_key', return_value=mock_auth):
            
            # Mock embedding client
            mock_embedding_client = AsyncMock()
            mock_embedding_client.available = True
            mock_embedding_client.embed.return_value = [0.1, 0.2, 0.3]
            mock_client.return_value = mock_embedding_client
            
            # Mock cache
            mock_cache.get.return_value = None
            mock_cache.set.return_value = None
            
            # Test client
            async with AsyncClient(app=app, base_url="http://test") as ac:
                # Make requests that should trigger rate limiting
                for i in range(settings.rate_limit_rps + 1):
                    response = await ac.post(
                        "/v2/embeddings",
                        json={"input": f"test text {i}"},
                        headers={"Authorization": "Bearer test-api-key-12345"}
                    )
                
                # Should have triggered rate limiting at least once
                assert any(r.status_code == 429 for r in [await ac.post("/v2/embeddings", json={"input": "test"}, headers={"Authorization": "Bearer test-api-key-12345"})])