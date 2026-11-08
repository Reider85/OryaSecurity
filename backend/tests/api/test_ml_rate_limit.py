"""Tests for ML endpoints rate limiting."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.api.v2.classify import router as classify_router
from app.api.v2.embeddings import router as embeddings_router
from app.config import settings
from app.core.auth import AuthInfo, verify_api_key
from app.core.metrics import scanner_embedding_requests_total
from app.core.rate_limit import RateLimiter


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
    return AuthInfo(api_key="test-api-key-12345", tenant_id="default")


@pytest.fixture
def rate_limiter():
    """Create rate limiter for testing."""
    return RateLimiter(max_rps=2)  # 2 RPS for testing


def _embedding_client_mock() -> AsyncMock:
    client = AsyncMock()
    client.available = True
    client.embed.return_value = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    return client


def _classifier_mock() -> MagicMock:
    result = MagicMock()
    result.top_class = "benign"
    result.confidence = 0.95
    result.to_dict.return_value = {
        "top_class": "benign",
        "confidence": 0.95,
        "all_classes": {"benign": 0.95, "prompt_injection": 0.05},
    }
    classifier = MagicMock()
    classifier.available = True
    classifier.classify = AsyncMock(return_value=result)
    classifier.should_flag = MagicMock(return_value=False)
    return classifier


class TestEmbeddingsRateLimiting:
    """Test rate limiting for embeddings endpoint."""

    @pytest.mark.asyncio
    async def test_embeddings_rate_limit_normal(self, app, mock_auth, monkeypatch):
        """Test normal rate limiting behavior for embeddings."""
        monkeypatch.setattr(settings, "rate_limit_rps", 2)
        app.dependency_overrides[verify_api_key] = lambda: mock_auth
        with (
            patch("app.api.v2.embeddings.get_shared_embedding_client") as mock_client,
            patch(
                "app.api.v2.embeddings.embedding_cache", new_callable=AsyncMock
            ) as mock_cache,
            patch("app.api.v2.embeddings.rate_limiter", RateLimiter(max_rps=2)),
        ):
            mock_client.return_value = _embedding_client_mock()
            mock_cache.get.return_value = None
            mock_cache.set.return_value = None

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                # First request should succeed
                response1 = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text"},
                    headers={"Authorization": "Bearer test-api-key-12345"},
                )
                assert response1.status_code == 200
                assert "X-RateLimit-Limit" in response1.headers
                assert "X-RateLimit-Remaining" in response1.headers

                # Second request should succeed (within 2 RPS limit)
                response2 = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text 2"},
                    headers={"Authorization": "Bearer test-api-key-12345"},
                )
                assert response2.status_code == 200

                # Third request should be rate limited
                response3 = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text 3"},
                    headers={"Authorization": "Bearer test-api-key-12345"},
                )
                assert response3.status_code == 429
                assert response3.headers["Retry-After"] == "1"
                assert response3.headers["X-RateLimit-Limit"] == "2"
                assert response3.headers["X-RateLimit-Remaining"] == "0"

    @pytest.mark.asyncio
    async def test_embeddings_rate_limit_different_keys(
        self, app, mock_auth, monkeypatch
    ):
        """Test rate limiting with different API keys."""
        monkeypatch.setattr(settings, "rate_limit_rps", 2)
        auth_holder = {"info": AuthInfo(api_key="key1", tenant_id="default")}
        app.dependency_overrides[verify_api_key] = lambda: auth_holder["info"]
        with (
            patch("app.api.v2.embeddings.get_shared_embedding_client") as mock_client,
            patch(
                "app.api.v2.embeddings.embedding_cache", new_callable=AsyncMock
            ) as mock_cache,
            patch("app.api.v2.embeddings.rate_limiter", RateLimiter(max_rps=2)),
        ):
            mock_client.return_value = _embedding_client_mock()
            mock_cache.get.return_value = None
            mock_cache.set.return_value = None

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                # First API key - 2 requests
                response1 = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text"},
                    headers={"Authorization": "Bearer key1"},
                )
                assert response1.status_code == 200

                response2 = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text"},
                    headers={"Authorization": "Bearer key1"},
                )
                assert response2.status_code == 200

                # Second API key - should not be affected by first key's limits
                auth_holder["info"] = AuthInfo(api_key="key2", tenant_id="default")

                response3 = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text"},
                    headers={"Authorization": "Bearer key2"},
                )
                assert response3.status_code == 200

                response4 = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text"},
                    headers={"Authorization": "Bearer key2"},
                )
                assert response4.status_code == 200

                # Both keys should now be rate limited
                response5 = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text"},
                    headers={"Authorization": "Bearer key1"},
                )
                assert response5.status_code == 429

                response6 = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text"},
                    headers={"Authorization": "Bearer key2"},
                )
                assert response6.status_code == 429

    @pytest.mark.asyncio
    async def test_embeddings_rate_limit_headers(self, app, mock_auth):
        """Test rate limit headers in responses."""
        app.dependency_overrides[verify_api_key] = lambda: mock_auth
        with (
            patch("app.api.v2.embeddings.get_shared_embedding_client") as mock_client,
            patch(
                "app.api.v2.embeddings.embedding_cache", new_callable=AsyncMock
            ) as mock_cache,
            patch(
                "app.api.v2.embeddings.rate_limiter",
                RateLimiter(max_rps=settings.rate_limit_rps),
            ),
        ):
            mock_client.return_value = _embedding_client_mock()
            mock_cache.get.return_value = None
            mock_cache.set.return_value = None

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text"},
                    headers={"Authorization": "Bearer test-api-key-12345"},
                )

                assert response.status_code == 200
                assert response.headers["X-RateLimit-Limit"] == str(
                    settings.rate_limit_rps
                )
                assert "X-RateLimit-Remaining" in response.headers
                assert isinstance(int(response.headers["X-RateLimit-Remaining"]), int)


class TestClassificationRateLimiting:
    """Test rate limiting for classification endpoint."""

    @pytest.mark.asyncio
    async def test_classification_rate_limit_normal(self, app, mock_auth, monkeypatch):
        """Test normal rate limiting behavior for classification."""
        monkeypatch.setattr(settings, "rate_limit_rps", 2)
        app.dependency_overrides[verify_api_key] = lambda: mock_auth
        with (
            patch("app.api.v2.classify.get_classifier") as mock_classifier,
            patch("app.api.v2.classify.rate_limiter", RateLimiter(max_rps=2)),
        ):
            mock_classifier.return_value = _classifier_mock()

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                # First request should succeed
                response1 = await ac.post(
                    "/v2/classify",
                    json={"text": "Hello, how are you?"},
                    headers={"Authorization": "Bearer test-api-key-12345"},
                )
                assert response1.status_code == 200
                assert "X-RateLimit-Limit" in response1.headers
                assert "X-RateLimit-Remaining" in response1.headers

                # Second request should succeed (within 2 RPS limit)
                response2 = await ac.post(
                    "/v2/classify",
                    json={"text": "What's the weather?"},
                    headers={"Authorization": "Bearer test-api-key-12345"},
                )
                assert response2.status_code == 200

                # Third request should be rate limited
                response3 = await ac.post(
                    "/v2/classify",
                    json={"text": "Can you help me?"},
                    headers={"Authorization": "Bearer test-api-key-12345"},
                )
                assert response3.status_code == 429
                assert response3.headers["Retry-After"] == "1"

    @pytest.mark.asyncio
    async def test_classification_rate_limit_concurrent(
        self, app, mock_auth, monkeypatch
    ):
        """Test rate limiting with concurrent requests."""
        monkeypatch.setattr(settings, "rate_limit_rps", 2)
        app.dependency_overrides[verify_api_key] = lambda: mock_auth
        with (
            patch("app.api.v2.classify.get_classifier") as mock_classifier,
            patch("app.api.v2.classify.rate_limiter", RateLimiter(max_rps=2)),
        ):
            mock_classifier.return_value = _classifier_mock()

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                # Make 3 concurrent requests
                tasks = []
                for i in range(3):
                    task = ac.post(
                        "/v2/classify",
                        json={"text": f"Concurrent request {i}"},
                        headers={"Authorization": "Bearer test-api-key-12345"},
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
    async def test_rate_limit_error_handling(self, app, mock_auth, monkeypatch):
        """Test rate limiter error handling."""
        monkeypatch.setattr(settings, "rate_limit_rps", 5)
        app.dependency_overrides[verify_api_key] = lambda: mock_auth
        with (
            patch("app.api.v2.embeddings.get_shared_embedding_client") as mock_client,
            patch(
                "app.api.v2.embeddings.embedding_cache", new_callable=AsyncMock
            ) as mock_cache,
            patch("app.api.v2.embeddings.rate_limiter", RateLimiter(max_rps=5)),
        ):
            mock_client.return_value = _embedding_client_mock()
            mock_cache.get.return_value = None
            mock_cache.set.return_value = None

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                # Exhaust rate limit
                for i in range(settings.rate_limit_rps):
                    response = await ac.post(
                        "/v2/embeddings",
                        json={"input": f"test text {i}"},
                        headers={"Authorization": "Bearer test-api-key-12345"},
                    )
                    assert response.status_code == 200

                # Next request should be rate limited
                response = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text over limit"},
                    headers={"Authorization": "Bearer test-api-key-12345"},
                )
                assert response.status_code == 429

    @pytest.mark.asyncio
    async def test_rate_limit_reset_after_window(self, app, mock_auth, monkeypatch):
        """Test rate limiter reset after sliding window."""
        monkeypatch.setattr(settings, "rate_limit_rps", 5)
        app.dependency_overrides[verify_api_key] = lambda: mock_auth
        with (
            patch("app.api.v2.embeddings.get_shared_embedding_client") as mock_client,
            patch(
                "app.api.v2.embeddings.embedding_cache", new_callable=AsyncMock
            ) as mock_cache,
            patch("app.api.v2.embeddings.rate_limiter", RateLimiter(max_rps=5)),
        ):
            mock_client.return_value = _embedding_client_mock()
            mock_cache.get.return_value = None
            mock_cache.set.return_value = None

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                # Exhaust rate limit
                for i in range(settings.rate_limit_rps):
                    response = await ac.post(
                        "/v2/embeddings",
                        json={"input": f"test text {i}"},
                        headers={"Authorization": "Bearer test-api-key-12345"},
                    )
                    assert response.status_code == 200

                # Should be rate limited
                response = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text"},
                    headers={"Authorization": "Bearer test-api-key-12345"},
                )
                assert response.status_code == 429

                # Wait for the sliding window to pass
                await asyncio.sleep(1.1)

                # Should succeed again after the window reset
                response = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test text after window"},
                    headers={"Authorization": "Bearer test-api-key-12345"},
                )
                assert response.status_code == 200


class TestRateLimitMetrics:
    """Test rate limiting metrics."""

    @pytest.mark.asyncio
    async def test_rate_limit_metrics_increased(self, app, mock_auth, monkeypatch):
        """Test that rate limiting metrics are properly incremented."""
        monkeypatch.setattr(settings, "rate_limit_rps", 5)
        app.dependency_overrides[verify_api_key] = lambda: mock_auth
        with (
            patch("app.api.v2.embeddings.get_shared_embedding_client") as mock_client,
            patch(
                "app.api.v2.embeddings.embedding_cache", new_callable=AsyncMock
            ) as mock_cache,
            patch("app.api.v2.embeddings.rate_limiter", RateLimiter(max_rps=5)),
        ):
            mock_client.return_value = _embedding_client_mock()
            mock_cache.get.return_value = None
            mock_cache.set.return_value = None

            initial_requests = scanner_embedding_requests_total._value._value

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                # Make requests that should trigger rate limiting
                for i in range(settings.rate_limit_rps + 1):
                    await ac.post(
                        "/v2/embeddings",
                        json={"input": f"test text {i}"},
                        headers={"Authorization": "Bearer test-api-key-12345"},
                    )

                # Should have triggered rate limiting at least once
                response = await ac.post(
                    "/v2/embeddings",
                    json={"input": "test"},
                    headers={"Authorization": "Bearer test-api-key-12345"},
                )
                assert response.status_code == 429

            # Successful requests increment the counter
            assert (
                scanner_embedding_requests_total._value._value == initial_requests + 5
            )
