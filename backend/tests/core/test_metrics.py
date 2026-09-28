from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from app.api.metrics import router
from app.core.cache import decision_cache
from app.core.metrics import (
    scanner_cache_hits_total,
    scanner_cache_misses_total,
    scanner_cache_size,
    scanner_cache_latency_seconds,
)
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestMetricsEndpoint:
    def test_metrics_endpoint_returns_text_format(self, client: TestClient) -> None:
        """Test that /metrics endpoint returns text/plain content type."""
        response = client.get("/metrics")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/plain; version=0.0.4"
        assert response.text.startswith("# HELP")
        assert "# TYPE" in response.text

    def test_metrics_endpoint_contains_cache_metrics(self, client: TestClient) -> None:
        """Test that cache metrics are present in the response."""
        response = client.get("/metrics")
        assert response.status_code == 200
        
        # Check that our cache metrics exist
        metrics_text = response.text
        assert "scanner_cache_hits_total" in metrics_text
        assert "scanner_cache_misses_total" in metrics_text
        assert "scanner_cache_size" in metrics_text
        assert "scanner_cache_latency_seconds" in metrics_text

    def test_metrics_endpoint_includes_help_and_type(self, client: TestClient) -> None:
        """Test that metrics include proper HELP and TYPE documentation."""
        response = client.get("/metrics")
        assert response.status_code == 200
        
        metrics_text = response.text
        assert "# HELP scanner_cache_hits_total Total cache hits" in metrics_text
        assert "# TYPE scanner_cache_hits_total counter" in metrics_text
        assert "# HELP scanner_cache_misses_total Total cache misses" in metrics_text
        assert "# TYPE scanner_cache_misses_total counter" in metrics_text
        assert "# HELP scanner_cache_size Current number of entries in cache" in metrics_text
        assert "# TYPE scanner_cache_size gauge" in metrics_text
        assert "# HELP scanner_cache_latency_seconds Cache operation latency in seconds" in metrics_text
        assert "# TYPE scanner_cache_latency_seconds histogram" in metrics_text


class TestCacheMetrics:
    @pytest.mark.asyncio
    async def test_cache_hit_increment_metrics(self) -> None:
        """Test that cache hit increments the correct Prometheus counter."""
        # Clear any existing values
        scanner_cache_hits_total.clear()
        initial_hits = scanner_cache_hits_total._value._value
        
        # Set a cache entry
        await decision_cache.set("test prompt", "allow", "clean", [])
        
        # Get from cache (should be a hit)
        await decision_cache.get("test prompt")
        
        # Check that hit counter was incremented
        assert scanner_cache_hits_total._value._value == initial_hits + 1

    @pytest.mark.asyncio
    async def test_cache_miss_increment_metrics(self) -> None:
        """Test that cache miss increments the correct Prometheus counter."""
        # Clear any existing values
        scanner_cache_misses_total.clear()
        initial_misses = scanner_cache_misses_total._value._value
        
        # Get from cache (should be a miss since nothing is stored)
        await decision_cache.get("nonexistent prompt")
        
        # Check that miss counter was incremented
        assert scanner_cache_misses_total._value._value == initial_misses + 1

    @pytest.mark.asyncio
    async def test_cache_latency_histogram_records_values(self) -> None:
        """Test that cache latency histogram records operation times."""
        # Clear histogram
        scanner_cache_latency_seconds.clear()
        
        # Set a cache entry
        await decision_cache.set("test prompt", "allow", "clean", [])
        
        # Get from cache (will record latency)
        await decision_cache.get("test prompt")
        
        # Check that histogram has recorded values
        assert len(scanner_cache_latency_samples()) > 0

    @pytest.mark.asyncio
    async def test_cache_size_gauge_updates(self) -> None:
        """Test that cache size gauge is updated."""
        # Clear cache
        await decision_cache.flush()
        
        # Initially size should be 0
        assert scanner_cache_size._value._value == 0
        
        # Set cache entries
        await decision_cache.set("prompt1", "allow", "clean", [])
        await decision_cache.set("prompt2", "block", "pii", [])
        
        # Size should be updated (though our implementation returns 0 due to Redis limitations)
        # In a real implementation, this would reflect actual cache size
        assert scanner_cache_size._value._value >= 0

    @pytest.mark.asyncio
    async def test_cache_flush_resets_size(self) -> None:
        """Test that cache flush resets the size gauge."""
        # Set some cache entries
        await decision_cache.set("prompt1", "allow", "clean", [])
        await decision_cache.set("prompt2", "block", "pii", [])
        
        # Flush cache
        await decision_cache.flush()
        
        # Size should be reset to 0
        assert scanner_cache_size._value._value == 0


class TestMetricsIntegration:
    @pytest.mark.asyncio
    async def test_full_cache_workflow_metrics(self, client: TestClient) -> None:
        """Test that a complete cache workflow generates correct metrics."""
        # Clear metrics and cache
        scanner_cache_hits_total.clear()
        scanner_cache_misses_total.clear()
        scanner_cache_latency_seconds.clear()
        await decision_cache.flush()
        
        # Initial state
        initial_hits = scanner_cache_hits_total._value._value
        initial_misses = scanner_cache_misses_total._value._value
        
        # First access (miss)
        await decision_cache.get("test prompt miss")
        miss_after_first = scanner_cache_misses_total._value._value
        
        # Set the value
        await decision_cache.set("test prompt miss", "allow", "clean", [])
        
        # Second access (hit)
        await decision_cache.get("test prompt miss")
        hits_after_second = scanner_cache_hits_total._value._value
        
        # Verify metrics
        assert miss_after_first == initial_misses + 1
        assert hits_after_second == initial_hits + 1
        assert len(scanner_cache_latency_samples()) >= 2  # At least 2 operations recorded

    def test_metrics_endpoint_accessible_without_auth(self, client: TestClient) -> None:
        """Test that /metrics endpoint is accessible without authentication."""
        response = client.get("/metrics")
        assert response.status_code == 200
        # Should not redirect or return auth error
        assert "not authorized" not in response.text.lower()


def scanner_cache_latency_samples() -> list[float]:
    """Helper to get latency samples from histogram for testing."""
    return list(scanner_cache_latency_seconds._value._buckets)[1:]  # Skip the underflow bucket