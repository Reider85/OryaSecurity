from __future__ import annotations

import pytest
import numpy as np
from unittest.mock import AsyncMock, patch
from prometheus_client import REGISTRY

from app.core.detectors.vector_search import VectorSearcher, SearchResult


class TestVectorSearcher:
    """VectorSearcher unit tests with hand-rolled stub client."""

    @pytest.fixture
    def stub_client(self):
        """Stub qdrant client with mock query_points method."""
        client = AsyncMock()
        client.query_points.return_value = AsyncMock(points=[
            AsyncMock(id="test-1", score=0.95, payload={"type": "jailbreak"}),
            AsyncMock(id="test-2", score=0.87, payload=None),
        ])
        return client

    @pytest.fixture
    def searcher(self, stub_client):
        """VectorSearcher instance with stub client."""
        return VectorSearcher(
            url="http://test-qdrant:6333",
            collection="test_collection",
            top_k=5,
            ef_search=32,
            qdrant_client=stub_client,
        )

    @pytest.fixture
    def searcher_with_ef(self, stub_client):
        """VectorSearcher instance with ef_search enabled."""
        return VectorSearcher(
            url="http://test-qdrant:6333",
            collection="test_collection",
            top_k=5,
            ef_search=32,
            qdrant_client=stub_client,
        )

    @pytest.fixture
    def embedding(self):
        """Test embedding vector."""
        return np.random.rand(1024).astype(np.float32)

    @pytest.mark.asyncio
    async def test_successful_search(self, searcher, stub_client, embedding):
        """Successful vector search with payload mapping."""
        results = await searcher.search(embedding)
    
        assert len(results) == 2
        assert results[0].id == "test-1"
        assert results[0].score == 0.95
        assert results[0].payload == {"type": "jailbreak"}
        assert results[1].id == "test-2"
        assert results[1].score == 0.87
        assert results[1].payload == {}
    
        # Verify query_points call
        stub_client.query_points.assert_called_once()
        call_args = stub_client.query_points.call_args
        assert call_args.kwargs["collection_name"] == "test_collection"
        assert call_args.kwargs["query"] == embedding.tolist()
        assert call_args.kwargs["limit"] == 5
        assert call_args.kwargs["with_payload"] is True

    @pytest.mark.asyncio
    async def test_search_with_ef_search(self, searcher_with_ef, stub_client, embedding):
        """Vector search with HNSW ef parameter."""
        results = await searcher_with_ef.search(embedding)
        
        assert len(results) == 2
        assert results[0].id == "test-1"
        assert results[0].score == 0.95
        assert results[0].payload == {"type": "jailbreak"}
        assert results[1].id == "test-2"
        assert results[1].score == 0.87
        assert results[1].payload == {}
        
        # Verify query_points call with ef parameter
        stub_client.query_points.assert_called_once()
        call_args = stub_client.query_points.call_args
        assert call_args.kwargs["collection_name"] == "test_collection"
        assert call_args.kwargs["limit"] == 5
        assert call_args.kwargs["with_payload"] is True
        
        # Check if ef parameter is passed correctly
        from qdrant_client import models
        assert isinstance(call_args.kwargs["query"], models.Query)
        assert call_args.kwargs["query"].vector.ef == 32

    @pytest.mark.asyncio
    async def test_top_k_default(self, searcher, stub_client, embedding):
        """Default top_k from constructor."""
        searcher.top_k = 10
        results = await searcher.search(embedding, top_k=None)

        stub_client.query_points.assert_called_once_with(
            collection_name="test_collection",
            query=embedding.tolist(),
            limit=10,
            with_payload=True,
            search_params=None,  # ef_search=32 not set in this call
        )

    @pytest.mark.asyncio
    async def test_top_k_override(self, searcher, stub_client, embedding):
        """Override top_k in search call."""
        results = await searcher.search(embedding, top_k=3)

        stub_client.query_points.assert_called_once_with(
            collection_name="test_collection",
            query=embedding.tolist(),
            limit=3,
            with_payload=True,
            search_params=stub_client.query_points.call_args.kwargs["search_params"],
        )

    @pytest.mark.asyncio
    async def test_ef_search_none(self):
        """VectorSearcher without ef_search (no search_params)."""
        searcher = VectorSearcher(
            url="http://test-qdrant:6333",
            collection="test_collection",
            ef_search=None,
        )
        stub_client = AsyncMock()
        stub_client.query_points.return_value = AsyncMock(points=[])
        
        with patch("app.core.detectors.vector_search.AsyncQdrantClient", return_value=stub_client):
            results = await searcher.search(np.zeros(1024).astype(np.float32))

        stub_client.query_points.assert_called_once_with(
            collection_name="test_collection",
            query=[0.0] * 1024,
            limit=10,
            with_payload=True,
        )
        # No search_params when ef_search is None
        assert "search_params" not in stub_client.query_points.call_args.kwargs

    @pytest.mark.asyncio
    async def test_degrade_on_exception(self, searcher, embedding):
        """Degrade gracefully on client exception."""
        stub_client = searcher._client
        stub_client.query_points.side_effect = Exception("Connection failed")

        results = await searcher.search(embedding)

        assert results == []
        stub_client.query_points.assert_called_once()

        # Verify degraded metric incremented
        from app.core.metrics import scanner_post_factum_degraded_total
        degraded_metric = scanner_post_factum_degraded_total._value._value["vector_search"]
        assert degraded_metric == 1

        # Verify warning logged
        with patch("app.core.detectors.vector_search.logger") as mock_logger:
            mock_logger.warning.assert_called_once_with(
                "vector_search_degraded",
                collection="test_collection",
                error="Connection failed",
            )

    @pytest.mark.asyncio
    async def test_latency_metric_on_success(self, searcher, embedding):
        """Latency metric recorded on successful search."""
        from app.core.metrics import scanner_vector_search_duration_seconds

        results = await searcher.search(embedding)
        assert results == []

        # Verify latency metric observed
        sample_value = REGISTRY.get_sample_value(
            "scanner_vector_search_duration_seconds_sum"
        )
        assert sample_value is not None
        assert sample_value >= 0

    @pytest.mark.asyncio
    async def test_latency_metric_on_exception(self, searcher, embedding):
        """Latency metric recorded on exception."""
        from app.core.metrics import scanner_vector_search_duration_seconds

        stub_client = searcher._client
        stub_client.query_points.side_effect = Exception("Connection failed")

        results = await searcher.search(embedding)
        assert results == []

        # Verify latency metric observed
        sample_value = REGISTRY.get_sample_value(
            "scanner_vector_search_duration_seconds_sum"
        )
        assert sample_value is not None
        assert sample_value >= 0

    @pytest.mark.asyncio
    async def test_lazy_client_creation(self):
        """Lazy client creation when qdrant_client=None."""
        searcher = VectorSearcher(
            url="http://test-qdrant:6333",
            collection="test_collection",
        )

        assert searcher._client is None

        with patch("app.core.detectors.vector_search.AsyncQdrantClient") as mock_client:
            mock_client.return_value = AsyncMock()
            results = await searcher.search(np.zeros(1024).astype(np.float32))

        mock_client.assert_called_once_with(url="http://test-qdrant:6333", timeout=2.0)
        assert searcher._client is not None

    @pytest.mark.asyncio
    async def test_close_owned_client(self, searcher, stub_client):
        """Close method closes only owned client."""
        # Mock client close method
        stub_client.close = AsyncMock()

        await searcher.close()

        stub_client.close.assert_called_once()
        assert searcher._client is None

    @pytest.mark.asyncio
    async def test_close_not_owned_client(self):
        """Close method does not close injected client."""
        stub_client = AsyncMock()
        searcher = VectorSearcher(
            url="http://test-qdrant:6333",
            collection="test_collection",
            qdrant_client=stub_client,
        )

        await searcher.close()

        stub_client.close.assert_not_called()
        assert searcher._client is not None  # Should still reference the injected client

    def test_singleton_get_set(self):
        """Test shared singleton accessors."""
        from app.core.detectors.vector_search import (
            get_shared_vector_searcher,
            set_shared_vector_searcher,
        )

        searcher = VectorSearcher("http://test", "test")
        set_shared_vector_searcher(searcher)
        assert get_shared_vector_searcher() is searcher

        set_shared_vector_searcher(None)
        assert get_shared_vector_searcher() is None

    @pytest.mark.asyncio
    async def test_embedding_conversion(self, searcher, stub_client):
        """Embedding numpy array converted to list in query."""
        test_embedding = np.array([0.1, 0.2, 0.3])
        await searcher.search(test_embedding)

        stub_client.query_points.assert_called_once()
        call_args = stub_client.query_points.call_args
        assert call_args.kwargs["query"] == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_empty_payload_handling(self, searcher, embedding):
        """Test handling of points with None payload."""
        stub_client = searcher._client
        stub_client.query_points.return_value = AsyncMock(points=[
            AsyncMock(id="test-1", score=0.95, payload=None),
            AsyncMock(id="test-2", score=0.87, payload={}),
        ])

        results = await searcher.search(embedding)

        assert len(results) == 2
        assert results[0].payload == {}
        assert results[1].payload == {}

    @pytest.mark.asyncio
    async def test_no_points_response(self, searcher, embedding):
        """Test response with no points."""
        stub_client = searcher._client
        stub_client.query_points.return_value = AsyncMock(points=[])

        results = await searcher.search(embedding)

        assert results == []
        stub_client.query_points.assert_called_once()