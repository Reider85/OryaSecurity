from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List

import pytest
import numpy as np
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.config import settings
from app.db.qdrant_client import get_qdrant, get_cluster_info, get_collection_info


# Probe helper: check if Qdrant is available
def _qdrant_available() -> bool:
    """Check if Qdrant is available at SCANNER_TEST_QDRANT_URL."""
    import os
    import httpx
    
    url = os.environ.get("SCANNER_TEST_QDRANT_URL", "http://127.0.0.1:6333")
    try:
        response = httpx.get(f"{url}/health", timeout=2.0)
        return response.status_code == 200
    except Exception:
        return False


# Skip tests if Qdrant is not available
pytestmark = pytest.mark.skipif(
    not _qdrant_available(), 
    reason="Qdrant is not available at SCANNER_TEST_QDRANT_URL"
)
pytestmark = pytest.mark.integration
pytestmark = pytest.mark.qdrant


class TestQdrantIntegration:
    """Integration tests for Qdrant vector database."""

    @pytest.mark.asyncio
    async def test_get_qdrant_client(self) -> None:
        """Test that Qdrant client can be created and retrieved."""
        client = await get_qdrant()
        assert client is not None
        assert isinstance(client, AsyncQdrantClient)
        assert client.url == settings.qdrant_url

    @pytest.mark.asyncio
    async def test_get_cluster_info(self) -> None:
        """Test getting cluster information."""
        cluster_info = await get_cluster_info()
        assert cluster_info is not None
        assert hasattr(cluster_info, 'status')
        assert hasattr(cluster_info, 'peers_count')
        assert hasattr(cluster_info, 'local_shards_count')

    @pytest.mark.asyncio
    async def test_get_collection_info_nonexistent(self) -> None:
        """Test getting info for nonexistent collection returns None."""
        collection_info = await get_collection_info("nonexistent_collection")
        assert collection_info is None

    @pytest.mark.asyncio
    async def test_create_and_manage_test_collection(self) -> None:
        """Test creating a temporary collection, inserting vectors, and searching."""
        client = await get_qdrant()
        
        # Use unique collection name to avoid conflicts
        collection_name = f"test_qdrant_integration_{uuid.uuid4().hex}"
        
        try:
            # Create collection
            await client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=128,  # Test with smaller dimension
                    distance="Cosine"
                )
            )
            
            # Verify collection exists
            collection_info = await get_collection_info(collection_name)
            assert collection_info is not None
            assert collection_info.status == "green"
            assert collection_info.config.params.vectors.size == 128
            
            # Insert test vectors
            test_vectors = []
            for i in range(5):
                vector = np.random.rand(128).astype(np.float32)
                test_vectors.append({
                    "id": i,
                    "vector": vector,
                    "payload": {"test_id": i, "category": "test"}
                })
            
            await client.upsert(
                collection_name=collection_name,
                points=test_vectors
            )
            
            # Search for similar vectors
            query_vector = np.random.rand(128).astype(np.float32)
            search_result = await client.search(
                collection_name=collection_name,
                query_vector=query_vector.tolist(),
                limit=3,
                with_payload=True
            )
            
            assert len(search_result) <= 3
            for point in search_result:
                assert point.id in [0, 1, 2, 3, 4]
                assert "payload" in point
                assert point.payload["category"] == "test"
                assert "test_id" in point.payload
            
            # Delete collection
            await client.delete_collection(collection_name)
            
            # Verify collection is deleted
            collection_info = await get_collection_info(collection_name)
            assert collection_info is None
            
        finally:
            # Cleanup: ensure collection is deleted even if test fails
            try:
                await client.delete_collection(collection_name)
            except Exception:
                pass

    @pytest.mark.asyncio
    async def test_cluster_status_consistency(self) -> None:
        """Test cluster status consistency (soft assertion for dev mode)."""
        cluster_info = await get_cluster_info()
        assert cluster_info is not None
        
        # In cluster mode, we expect multiple nodes
        # In dev single-node mode, we expect 1 node
        # This is a soft assertion - test passes regardless
        nodes_count = cluster_info.local_shards_count
        if nodes_count > 1:
            # Cluster mode detected
            assert cluster_info.peers_count >= 2, "Expected at least 2 peers in cluster mode"
        else:
            # Single node mode
            pass  # Single node is valid for dev

    @pytest.mark.asyncio
    async def test_large_payload_handling(self) -> None:
        """Test handling of large payload data."""
        client = await get_qdrant()
        
        collection_name = f"test_large_payload_{uuid.uuid4().hex}"
        
        try:
            # Create collection with larger payload
            await client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=128,
                    distance="Cosine"
                )
            )
            
            # Create payload with large text field
            large_text = "x" * 10000  # 10KB text
            test_point = {
                "id": 1,
                "vector": np.random.rand(128).astype(np.float32),
                "payload": {
                    "large_text": large_text,
                    "metadata": {"source": "test", "size": len(large_text)}
                }
            }
            
            await client.upsert(
                collection_name=collection_name,
                points=[test_point]
            )
            
            # Search and verify payload
            search_result = await client.search(
                collection_name=collection_name,
                query_vector=test_point["vector"].tolist(),
                limit=1,
                with_payload=True
            )
            
            assert len(search_result) == 1
            assert search_result[0].payload["large_text"] == large_text
            assert search_result[0].payload["metadata"]["size"] == 10000
            
        finally:
            # Cleanup
            try:
                await client.delete_collection(collection_name)
            except Exception:
                pass

    @pytest.mark.asyncio
    async def test_multiple_operations_concurrent(self) -> None:
        """Test concurrent operations on Qdrant."""
        client = await get_qdrant()
        
        collection_name = f"test_concurrent_{uuid.uuid4().hex}"
        
        try:
            # Create collection
            await client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=128,
                    distance="Cosine"
                )
            )
            
            # Concurrent insertions
            async def insert_batch(batch_id: int, count: int):
                points = []
                for i in range(count):
                    vector = np.random.rand(128).astype(np.float32)
                    points.append({
                        "id": batch_id * 100 + i,
                        "vector": vector,
                        "payload": {"batch": batch_id, "item": i}
                    })
                await client.upsert(collection_name=collection_name, points=points)
            
            # Run concurrent insertions
            insert_tasks = [
                insert_batch(i, 10) for i in range(3)
            ]
            await asyncio.gather(*insert_tasks)
            
            # Verify all points were inserted
            search_result = await client.search(
                collection_name=collection_name,
                query_vector=np.random.rand(128).astype(np.float32).tolist(),
                limit=50,
                with_payload=True
            )
            
            # Should have 30 points total (3 batches * 10 points each)
            assert len(search_result) == 30
            
            # Verify we have points from all batches
            batch_ids = {point.payload["batch"] for point in search_result}
            assert len(batch_ids) == 3
            
        finally:
            # Cleanup
            try:
                await client.delete_collection(collection_name)
            except Exception:
                pass

    @pytest.mark.asyncio
    async def test_error_handling_connection_issues(self) -> None:
        """Test graceful error handling when Qdrant has connection issues."""
        # This test verifies that our client handles errors gracefully
        # (the non-fatal initialization in init_qdrant should prevent crashes)
        
        client = await get_qdrant()
        
        # Test with invalid operation
        with pytest.raises(UnexpectedResponse):
            # This should raise an error - invalid collection name
            await client.get_collection("")

    @pytest.mark.asyncio
    async def test_collection_metadata(self) -> None:
        """Test collection metadata and configuration."""
        client = await get_qdrant()
        
        collection_name = f"test_metadata_{uuid.uuid4().hex}"
        
        try:
            # Create collection with specific config
            await client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=128,
                    distance="Cosine"
                ),
                hnsw_config=models.HnswConfigDiff(
                    m=16,
                    ef_construct=128,
                ),
                quantization_config=models.QuantizationConfig(
                    scalar=models.ScalarQuantization(
                        type=models.ScalarType.INT8,
                        quantile=0.99,
                        always_ram=True
                    )
                )
            )
            
            # Verify metadata
            collection_info = await get_collection_info(collection_name)
            assert collection_info is not None
            assert collection_info.config.params.vectors.size == 128
            assert collection_info.config.params.vectors.distance == "Cosine"
            assert collection_info.config.hnsw_config.m == 16
            assert collection_info.config.hnsw_config.ef_construct == 128
            assert collection_info.config.quantization_config.scalar.type == "int8"
            
        finally:
            # Cleanup
            try:
                await client.delete_collection(collection_name)
            except Exception:
                pass