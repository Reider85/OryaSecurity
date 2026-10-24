"""Integration tests for Qdrant scalar quantization setup and benchmarking."""

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
from app.db.qdrant_setup import (
    ensure_collection,
    create_collection,
    enable_scalar_quantization,
    verify_collection_config,
    build_quantization_config,
    build_hnsw_config,
)


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
pytestmark = [
    pytest.mark.skipif(
        not _qdrant_available(), 
        reason="Qdrant is not available at SCANNER_TEST_QDRANT_URL"
    ),
    pytest.mark.integration,
    pytest.mark.qdrant,
]


class TestQdrantQuantizationSetup:
    """Integration tests for Qdrant quantization setup."""

    @pytest.mark.asyncio
    async def test_create_collection_with_quantization(self, qdrant_client: AsyncQdrantClient) -> None:
        """Test creating collection with scalar int8 quantization."""
        collection_name = f"test_quantized_{uuid.uuid4().hex}"
        
        try:
            # Create collection with quantization
            await create_collection(
                qdrant_client,
                collection_name,
                size=128,  # Test with smaller dimension
                quantized=True,
            )
            
            # Verify configuration
            config = await verify_collection_config(
                qdrant_client,
                collection_name,
                size=128,
                quantized=True,
            )
            
            # Assert quantization details
            assert config.quantization_config is not None
            assert config.quantization_config.scalar.type == "int8"
            assert config.quantization_config.scalar.quantile == 0.99
            assert config.quantization_config.scalar.always_ram is True
            assert config.hnsw_config.m == 16
            assert config.hnsw_config.ef_construct == 128
            
        finally:
            # Cleanup
            try:
                await qdrant_client.delete_collection(collection_name)
            except UnexpectedResponse:
                pass  # Collection might not exist

    @pytest.mark.asyncio
    async def test_create_collection_without_quantization(self, qdrant_client: AsyncQdrantClient) -> None:
        """Test creating collection without quantization."""
        collection_name = f"test_no_quant_{uuid.uuid4().hex}"
        
        try:
            # Create collection without quantization
            await create_collection(
                qdrant_client,
                collection_name,
                size=128,
                quantized=False,
            )
            
            # Verify configuration
            config = await verify_collection_config(
                qdrant_client,
                collection_name,
                size=128,
                quantized=False,
            )
            
            # Assert no quantization
            assert config.quantization_config is None
            assert config.hnsw_config.m == 16
            assert config.hnsw_config.ef_construct == 128
            
        finally:
            # Cleanup
            try:
                await qdrant_client.delete_collection(collection_name)
            except UnexpectedResponse:
                pass

    @pytest.mark.asyncio
    async def test_enable_quantization_on_existing_collection(self, qdrant_client: AsyncQdrantClient) -> None:
        """Test enabling quantization on existing collection without it."""
        collection_name = f"test_enable_quant_{uuid.uuid4().hex}"
        
        try:
            # Create collection without quantization
            await create_collection(
                qdrant_client,
                collection_name,
                size=128,
                quantized=False,
            )
            
            # Enable quantization
            await enable_scalar_quantization(qdrant_client, collection_name)
            
            # Verify quantization is now present
            config = await verify_collection_config(
                qdrant_client,
                collection_name,
                size=128,
                quantized=True,
            )
            
            assert config.quantization_config is not None
            assert config.quantization_config.scalar.type == "int8"
            assert config.quantization_config.scalar.quantile == 0.99
            assert config.quantization_config.scalar.always_ram is True
            
        finally:
            # Cleanup
            try:
                await qdrant_client.delete_collection(collection_name)
            except UnexpectedResponse:
                pass

    @pytest.mark.asyncio
    async def test_ensure_collection_with_quantization(self, qdrant_client: AsyncQdrantClient) -> None:
        """Test ensure_collection creates collection with quantization."""
        collection_name = f"test_ensure_quant_{uuid.uuid4().hex}"
        
        try:
            # Ensure collection exists with quantization
            await ensure_collection(
                qdrant_client,
                collection_name,
                size=128,
                quantized=True,
            )
            
            # Verify configuration
            config = await verify_collection_config(
                qdrant_client,
                collection_name,
                size=128,
                quantized=True,
            )
            
            assert config.quantization_config is not None
            assert config.quantization_config.scalar.type == "int8"
            
        finally:
            # Cleanup
            try:
                await qdrant_client.delete_collection(collection_name)
            except UnexpectedResponse:
                pass

    @pytest.mark.asyncio
    async def test_ensure_collection_idempotent(self, qdrant_client: AsyncQdrantClient) -> None:
        """Test ensure_collection is idempotent."""
        collection_name = f"test_ensure_idempotent_{uuid.uuid4().hex}"
        
        try:
            # First ensure
            await ensure_collection(
                qdrant_client,
                collection_name,
                size=128,
                quantized=True,
            )
            
            # Get creation time for comparison (approximate)
            info1 = await qdrant_client.get_collection(collection_name)
            creation_time1 = info1.status.created_at
            
            # Second ensure - should not recreate
            await ensure_collection(
                qdrant_client,
                collection_name,
                size=128,
                quantized=True,
            )
            
            info2 = await qdrant_client.get_collection(collection_name)
            creation_time2 = info2.status.created_at
            
            # Should be same collection (no recreation)
            assert creation_time1 == creation_time2
            
        finally:
            # Cleanup
            try:
                await qdrant_client.delete_collection(collection_name)
            except UnexpectedResponse:
                pass

    @pytest.mark.asyncio
    async def test_ensure_collection_force_recreate(self, qdrant_client: AsyncQdrantClient) -> None:
        """Test ensure_collection with force_recreate=True."""
        collection_name = f"test_ensure_force_{uuid.uuid4().hex}"
        
        try:
            # Create initial collection
            await create_collection(
                qdrant_client,
                collection_name,
                size=128,
                quantized=True,
            )
            
            # Force recreation
            await ensure_collection(
                qdrant_client,
                collection_name,
                force_recreate=True,
                size=128,
                quantized=True,
            )
            
            # Verify still exists with correct config
            config = await verify_collection_config(
                qdrant_client,
                collection_name,
                size=128,
                quantized=True,
            )
            
            assert config.quantization_config is not None
            assert config.quantization_config.scalar.type == "int8"
            
        finally:
            # Cleanup
            try:
                await qdrant_client.delete_collection(collection_name)
            except UnexpectedResponse:
                pass

    @pytest.mark.asyncio
    async def test_verify_collection_config_mismatch(self, qdrant_client: AsyncQdrantClient) -> None:
        """Test verify_collection_config raises on mismatch."""
        collection_name = f"test_verify_mismatch_{uuid.uuid4().hex}"
        
        try:
            # Create collection with wrong dimension
            await create_collection(
                qdrant_client,
                collection_name,
                size=64,  # Wrong dimension
                quantized=True,
            )
            
            # Verify should raise AssertionError
            with pytest.raises(AssertionError, match="Vector size mismatch"):
                await verify_collection_config(
                    qdrant_client,
                    collection_name,
                    size=128,  # Expected dimension
                    quantized=True,
                )
                
        finally:
            # Cleanup
            try:
                await qdrant_client.delete_collection(collection_name)
            except UnexpectedResponse:
                pass

    @pytest.mark.asyncio
    async def test_verify_collection_config_mismatch_quantization_type(self, qdrant_client: AsyncQdrantClient) -> None:
        """Test verify_collection_config raises on quantization type mismatch."""
        collection_name = f"test_verify_quant_mismatch_{uuid.uuid4().hex}"
        
        try:
            # Create collection with wrong quantization config
            await qdrant_client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(size=128, distance="Cosine"),
                hnsw_config=models.HnswConfigDiff(m=16, ef_construct=128),
                quantization_config=models.QuantizationConfig(
                    scalar=models.ScalarQuantization(
                        type=models.ScalarType.FLOAT32,  # Wrong type
                        quantile=0.99,
                        always_ram=True,
                    )
                ),
            )
            
            # Verify should raise AssertionError
            with pytest.raises(AssertionError, match="Quantization type mismatch"):
                await verify_collection_config(
                    qdrant_client,
                    collection_name,
                    size=128,
                    quantized=True,
                )
                
        finally:
            # Cleanup
            try:
                await qdrant_client.delete_collection(collection_name)
            except UnexpectedResponse:
                pass


class TestQdrantQuantizationBenchmark:
    """Integration tests for quantization benchmark."""

    @pytest.mark.asyncio
    async def test_benchmark_synthetic_recall(self, qdrant_client: AsyncQdrantClient) -> None:
        """Test synthetic benchmark with recall@10."""
        # Use smaller dimensions and counts for faster test
        dim = 64
        num_points = 300
        num_queries = 15
        top_k = 10
        
        # Generate synthetic corpus and queries
        from app.ml.benchmark_quantization import generate_synthetic_corpus, generate_synthetic_queries
        
        corpus = await generate_synthetic_corpus(num_points, dim, seed=42)
        queries = await generate_synthetic_queries(num_queries, corpus, dim, seed=42)
        
        collection_prefix = f"test_benchmark_{uuid.uuid4().hex}"
        
        try:
            # Run benchmark
            from app.ml.benchmark_quantization import run_benchmark
            report = await run_benchmark(
                qdrant_client,
                corpus,
                queries,
                top_k=top_k,
                threshold=0.95,
                collection_prefix=collection_prefix,
            )
            
            # Verify report structure
            assert report.num_queries == num_queries
            assert report.top_k == top_k
            assert report.threshold == 0.95
            assert len(report.recalls) == num_queries
            assert all(0.0 <= r <= 1.0 for r in report.recalls)  # Recall should be in [0, 1]
            
            # For synthetic data with rescore=True, recall should be high
            # Note: This might be flaky with very small datasets, but 300 points should be stable
            assert report.mean_recall >= 0.8  # Relaxed threshold for test
            
            # Test report serialization
            report_dict = report.to_dict()
            assert report_dict["num_queries"] == num_queries
            assert report_dict["mean_recall"] == report.mean_recall
            assert report_dict["passed"] == report.passed
            
        finally:
            # Cleanup is handled by run_benchmark's finally block
            pass

    @pytest.mark.asyncio
    async def test_compute_recall_at_k(self, qdrant_client: AsyncQdrantClient) -> None:
        """Test recall@k computation function."""
        from app.ml.benchmark_quantization import compute_recall_at_k
        
        # Test cases
        # Perfect match
        baseline = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
        quantized = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
        recall = compute_recall_at_k(baseline, quantized, 10)
        assert recall == 1.0
        
        # No match
        baseline = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
        quantized = ["k", "l", "m", "n", "o", "p", "q", "r", "s", "t"]
        recall = compute_recall_at_k(baseline, quantized, 10)
        assert recall == 0.0
        
        # Partial match (5/10)
        baseline = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
        quantized = ["a", "b", "c", "d", "e", "k", "l", "m", "n", "o"]
        recall = compute_recall_at_k(baseline, quantized, 10)
        assert recall == 0.5
        
        # More results than k
        baseline = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
        quantized = ["a", "b", "c", "x", "y", "z", "w", "v", "u", "t"]
        recall = compute_recall_at_k(baseline, quantized, 5)  # Only consider top 5
        assert recall == 3/5  # "a", "b", "c" match in top 5

    @pytest.mark.asyncio
    async def test_synthetic_vector_generation(self, qdrant_client: AsyncQdrantClient) -> None:
        """Test synthetic vector generation produces valid unit vectors."""
        from app.ml.benchmark_quantization import generate_synthetic_corpus, generate_synthetic_queries
        
        # Test corpus generation
        corpus = await generate_synthetic_corpus(100, dim=32, seed=42)
        assert len(corpus) == 100
        for vector in corpus:
            assert vector.shape == (32,)
            assert np.allclose(np.linalg.norm(vector), 1.0)  # Unit vector
            assert vector.dtype == np.float32
        
        # Test query generation
        queries = await generate_synthetic_queries(10, corpus, dim=32, seed=42)
        assert len(queries) == 10
        for query in queries:
            assert query.shape == (32,)
            assert np.allclose(np.linalg.norm(query), 1.0)  # Unit vector
            assert query.dtype == np.float32