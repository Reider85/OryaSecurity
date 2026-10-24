"""Unit tests for benchmark_quantization module."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from app.ml.benchmark_quantization import (
    RecallReport,
    compute_recall_at_k,
    generate_synthetic_corpus,
    generate_synthetic_queries,
    run_benchmark,
    benchmark_synthetic,
    create_test_collection,
    search_collection,
)


class TestRecallReport:
    """Test RecallReport dataclass and methods."""

    def test_recall_report_empty(self) -> None:
        """Test RecallReport with no queries."""
        report = RecallReport(num_queries=0, top_k=10, threshold=0.95)
        
        assert report.num_queries == 0
        assert report.mean_recall == 0.0
        assert report.min_recall == 0.0
        assert report.p05_recall == 0.0
        assert report.passed is False

    def test_recall_report_with_data(self) -> None:
        """Test RecallReport with actual recall values."""
        recalls = [0.8, 0.9, 1.0, 0.7, 0.95]
        report = RecallReport(num_queries=5, top_k=10, threshold=0.95, recalls=recalls)
        
        assert report.num_queries == 5
        assert report.mean_recall == pytest.approx(0.87)
        assert report.min_recall == 0.7
        assert report.p05_recall == 0.7  # 5th percentile of 5 values
        assert report.passed is False  # 0.87 < 0.95

    def test_recall_report_passed(self) -> None:
        """Test RecallReport passes threshold."""
        recalls = [0.96, 0.97, 0.98, 0.99, 1.0]
        report = RecallReport(num_queries=5, top_k=10, threshold=0.95, recalls=recalls)
        
        assert report.mean_recall == pytest.approx(0.98)
        assert report.passed is True

    def test_recall_report_to_dict(self) -> None:
        """Test RecallReport serialization to dict."""
        recalls = [0.8, 0.9]
        report = RecallReport(num_queries=2, top_k=10, threshold=0.95, recalls=recalls)
        
        data = report.to_dict()
        assert data["num_queries"] == 2
        assert data["top_k"] == 10
        assert data["threshold"] == 0.95
        assert data["mean_recall"] == pytest.approx(0.85)
        assert data["min_recall"] == 0.8
        assert data["p05_recall"] == 0.8
        assert data["passed"] is False
        assert "recalls" not in data  # Not included by default

    def test_recall_report_to_dict_with_per_query(self) -> None:
        """Test RecallReport serialization with per-query data."""
        recalls = [0.8, 0.9]
        report = RecallReport(num_queries=2, top_k=10, threshold=0.95, recalls=recalls)
        
        data = report.to_dict(include_per_query=True)
        assert "recalls" in data
        assert data["recalls"] == [0.8, 0.9]


class TestComputeRecallAtK:
    """Test recall@k computation function."""

    def test_perfect_match(self) -> None:
        """Test perfect recall when all top-k match."""
        baseline = ["a", "b", "c", "d", "e"]
        quantized = ["a", "b", "c", "d", "e"]
        recall = compute_recall_at_k(baseline, quantized, 5)
        assert recall == 1.0

    def test_no_match(self) -> None:
        """Test zero recall when no items match."""
        baseline = ["a", "b", "c", "d", "e"]
        quantized = ["f", "g", "h", "i", "j"]
        recall = compute_recall_at_k(baseline, quantized, 5)
        assert recall == 0.0

    def test_partial_match(self) -> None:
        """Test partial recall when some items match."""
        baseline = ["a", "b", "c", "d", "e"]
        quantized = ["a", "b", "x", "y", "z"]
        recall = compute_recall_at_k(baseline, quantized, 5)
        assert recall == 2/5  # 2 out of 5 match

    def test_k_smaller_than_lists(self) -> None:
        """Test recall when k is smaller than the lists."""
        baseline = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
        quantized = ["a", "b", "c", "x", "y", "z", "w", "v", "u", "t"]
        recall = compute_recall_at_k(baseline, quantized, 5)  # Only consider top 5
        assert recall == 3/5  # "a", "b", "c" match in top 5

    def test_k_larger_than_lists(self) -> None:
        """Test recall when k is larger than the lists."""
        baseline = ["a", "b", "c"]
        quantized = ["a", "b", "x", "y", "z"]
        recall = compute_recall_at_k(baseline, quantized, 10)  # k larger than lists
        assert recall == 2/3  # 2 out of 3 baseline items found in top 10

    def test_empty_lists(self) -> None:
        """Test recall with empty lists."""
        recall = compute_recall_at_k([], [], 5)
        assert recall == 0.0  # Division by zero case handled

    def test_duplicate_ids(self) -> None:
        """Test recall with duplicate IDs in results."""
        baseline = ["a", "b", "c", "d", "e"]
        quantized = ["a", "a", "b", "x", "y"]  # Duplicate "a"
        recall = compute_recall_at_k(baseline, quantized, 5)
        assert recall == 2/5  # Only unique IDs count for intersection


class TestSyntheticVectorGeneration:
    """Test synthetic vector generation functions."""

    @pytest.mark.asyncio
    async def test_generate_synthetic_corpus(self) -> None:
        """Test corpus generation produces valid unit vectors."""
        corpus = await generate_synthetic_corpus(100, dim=32, seed=42)
        
        assert len(corpus) == 100
        for vector in corpus:
            assert vector.shape == (32,)
            assert np.allclose(np.linalg.norm(vector), 1.0)  # Unit vector
            assert vector.dtype == np.float32

    @pytest.mark.asyncio
    async def test_generate_synthetic_queries(self) -> None:
        """Test query generation produces valid unit vectors."""
        # Generate test corpus
        corpus = await generate_synthetic_corpus(50, dim=32, seed=42)
        
        # Generate queries
        queries = await generate_synthetic_queries(10, corpus, dim=32, seed=42)
        
        assert len(queries) == 10
        for query in queries:
            assert query.shape == (32,)
            assert np.allclose(np.linalg.norm(query), 1.0)  # Unit vector
            assert query.dtype == np.float32

    @pytest.mark.asyncio
    async def test_synthetic_corpus_reproducibility(self) -> None:
        """Test synthetic corpus generation is reproducible with same seed."""
        corpus1 = await generate_synthetic_corpus(10, dim=16, seed=123)
        corpus2 = await generate_synthetic_corpus(10, dim=16, seed=123)
        
        # Should be identical
        for v1, v2 in zip(corpus1, corpus2):
            np.testing.assert_array_almost_equal(v1, v2, decimal=6)

    @pytest.mark.asyncio
    async def test_synthetic_queries_reproducibility(self) -> None:
        """Test synthetic query generation is reproducible with same seed."""
        corpus = await generate_synthetic_corpus(10, dim=16, seed=123)
        
        queries1 = await generate_synthetic_queries(5, corpus, dim=16, seed=456)
        queries2 = await generate_synthetic_queries(5, corpus, dim=16, seed=456)
        
        # Should be identical
        for q1, q2 in zip(queries1, queries2):
            np.testing.assert_array_almost_equal(q1, q2, decimal=6)


class TestBenchmarkFunctions:
    """Test benchmark functions with mocked Qdrant client."""

    @pytest.mark.asyncio
    async def test_create_test_collection(self) -> None:
        """Test create_test_collection function."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        # Mock create_collection and upsert calls
        mock_client.create_collection = AsyncMock()
        mock_client.upsert = AsyncMock()
        
        test_vectors = [np.random.rand(32).astype(np.float32) for _ in range(10)]
        
        await create_test_collection(
            mock_client,
            "test_collection",
            test_vectors,
            quantized=True,
        )
        
        # Verify create_collection was called with correct parameters
        mock_client.create_collection.assert_called_once()
        args, kwargs = mock_client.create_collection.call_args
        assert kwargs["collection_name"] == "test_collection"
        assert kwargs["vectors_config"].size == 32
        assert kwargs["quantization_config"] is not None
        
        # Verify upsert was called (multiple times for batching)
        assert mock_client.upsert.call_count > 0
        assert mock_client.upsert.call_count <= 1  # Should be called once due to small batch

    @pytest.mark.asyncio
    async def test_search_collection_baseline(self) -> None:
        """Test search_collection with full precision (baseline)."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        # Mock query_points response
        mock_response = MagicMock()
        mock_response.points = [
            MagicMock(payload={"id": "point_1"}),
            MagicMock(payload={"id": "point_2"}),
            MagicMock(payload={"id": "point_3"}),
        ]
        mock_client.query_points = AsyncMock(return_value=mock_response)
        
        query_vector = np.random.rand(32).astype(np.float32)
        result_ids = await search_collection(
            mock_client,
            "test_collection",
            query_vector,
            top_k=5,
            quantized=False,
        )
        
        # Verify query_points was called correctly
        mock_client.query_points.assert_called_once()
        args, kwargs = mock_client.query_points.call_args
        assert kwargs["collection_name"] == "test_collection"
        assert kwargs["limit"] == 5
        assert kwargs["search_params"] is None  # No quantization for baseline
        assert kwargs["with_payload"] == ["id"]
        
        # Verify result
        assert result_ids == ["point_1", "point_2", "point_3"]

    @pytest.mark.asyncio
    async def test_search_collection_quantized(self) -> None:
        """Test search_collection with quantization."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        # Mock query_points response
        mock_response = MagicMock()
        mock_response.points = [
            MagicMock(payload={"id": "point_a"}),
            MagicMock(payload={"id": "point_b"}),
        ]
        mock_client.query_points = AsyncMock(return_value=mock_response)
        
        query_vector = np.random.rand(32).astype(np.float32)
        result_ids = await search_collection(
            mock_client,
            "test_collection",
            query_vector,
            top_k=3,
            quantized=True,
        )
        
        # Verify query_points was called with quantization
        mock_client.query_points.assert_called_once()
        args, kwargs = mock_client.query_points.call_args
        assert kwargs["collection_name"] == "test_collection"
        assert kwargs["limit"] == 3
        assert kwargs["search_params"] is not None
        assert kwargs["search_params"].quantization.ignore is False
        assert kwargs["search_params"].quantization.rescore is True
        
        # Verify result
        assert result_ids == ["point_a", "point_b"]

    @pytest.mark.asyncio
    async def test_run_benchmark(self) -> None:
        """Test run_benchmark function."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        # Mock search_collection to return controlled results
        async def mock_search_collection(name, query_vector, top_k, quantized):
            if quantized:
                return ["quantized_1", "quantized_2", "quantized_3"]
            else:
                return ["baseline_1", "baseline_2", "baseline_3"]
        
        # Patch search_collection
        with patch('app.ml.benchmark_quantization.search_collection', side_effect=mock_search_collection):
            corpus = [np.random.rand(32).astype(np.float32) for _ in range(10)]
            queries = [np.random.rand(32).astype(np.float32) for _ in range(5)]
            
            report = await run_benchmark(
                mock_client,
                corpus,
                queries,
                top_k=3,
                threshold=0.95,
                collection_prefix="test_benchmark",
            )
            
            # Verify report
            assert report.num_queries == 5
            assert report.top_k == 3
            assert report.threshold == 0.95
            assert len(report.recalls) == 5
            assert all(r == 1.0 for r in report.recalls)  # Perfect match in mock
            
            # Verify collections were created and cleaned up
            assert mock_client.create_collection.call_count == 2  # baseline + quantized
            assert mock_client.delete_collection.call_count == 2  # cleanup

    @pytest.mark.asyncio
    async def test_run_benchmark_cleanup_on_error(self) -> None:
        """Test run_benchmark cleans up even if an error occurs."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        # Make create_collection raise an error
        mock_client.create_collection.side_effect = Exception("Test error")
        
        corpus = [np.random.rand(32).astype(np.float32) for _ in range(10)]
        queries = [np.random.rand(32).astype(np.float32) for _ in range(5)]
        
        with pytest.raises(Exception, match="Test error"):
            await run_benchmark(
                mock_client,
                corpus,
                queries,
                top_k=3,
                threshold=0.95,
                collection_prefix="test_benchmark",
            )
        
        # Even with error, cleanup should be attempted
        assert mock_client.delete_collection.call_count == 2  # baseline + quantized

    @pytest.mark.asyncio
    async def test_benchmark_synthetic(self) -> None:
        """Test benchmark_synthetic function."""
        mock_client = AsyncMock(spec=AsyncQdrantClient)
        
        # Mock run_benchmark
        with patch('app.ml.benchmark_quantization.run_benchmark') as mock_run_benchmark:
            mock_run_benchmark.return_value = RecallReport(
                num_queries=10,
                top_k=5,
                threshold=0.95,
                recalls=[0.9] * 10,
            )
            
            report = await benchmark_synthetic(
                mock_client,
                num_points=100,
                num_queries=10,
                dim=32,
                top_k=5,
                threshold=0.95,
            )
            
            # Verify run_benchmark was called with correct parameters
            mock_run_benchmark.assert_called_once()
            args, kwargs = mock_run_benchmark.call_args
            assert len(args[1]) == 100  # corpus is positional arg
            assert len(kwargs['queries']) == 10
            assert kwargs['top_k'] == 5
            assert kwargs['threshold'] == 0.95
            
            # Verify report
            assert report.num_queries == 10
            assert report.mean_recall == 0.9


class TestBenchmarkCLI:
    """Test benchmark CLI argument parsing and main function."""

    def test_build_parser(self) -> None:
        """Test argument parser builds correctly."""
        from app.ml.benchmark_quantization import build_parser
        
        parser = build_parser()
        
        # Test basic arguments
        args = parser.parse_args(["--synthetic", "--num-points", "500", "--num-queries", "25"])
        assert args.synthetic is True
        assert args.num_points == 500
        assert args.num_queries == 25
        
        # Test collection mode
        args = parser.parse_args(["--corpus", "test_collection", "--num-queries", "10"])
        assert args.corpus == "test_collection"
        assert args.num_queries == 10
        
        # Test error: no mode specified
        with pytest.raises(SystemExit):
            parser.parse_args([])
        
        # Test error: both modes specified
        with pytest.raises(SystemExit):
            parser.parse_args(["--synthetic", "--corpus", "test"])

    @pytest.mark.asyncio
    async def test_main_synthetic_mode(self) -> None:
        """Test main function in synthetic mode."""
        from app.ml.benchmark_quantization import main
        
        # Mock dependencies
        with patch('app.ml.benchmark_quantization.benchmark_synthetic') as mock_benchmark, \
             patch('app.ml.benchmark_quantization.AsyncQdrantClient') as mock_client_class:
            
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            mock_benchmark.return_value = RecallReport(
                num_queries=10,
                top_k=5,
                threshold=0.95,
                recalls=[0.96] * 10,  # Pass threshold
            )
            
            # Test successful run
            result = await main()
            assert result == 0
            
            # Verify benchmark was called
            mock_benchmark.assert_called_once()
            args, kwargs = mock_benchmark.call_args
            assert kwargs['num_points'] == 100
            assert kwargs['num_queries'] == 10

    @pytest.mark.asyncio
    async def test_main_collection_mode(self) -> None:
        """Test main function in collection mode."""
        from app.ml.benchmark_quantization import main
        
        # Mock dependencies
        with patch('app.ml.benchmark_quantization.benchmark_from_collection') as mock_benchmark, \
             patch('app.ml.benchmark_quantization.AsyncQdrantClient') as mock_client_class:
            
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            mock_benchmark.return_value = RecallReport(
                num_queries=5,
                top_k=10,
                threshold=0.95,
                recalls=[0.9] * 5,  # Fail threshold
            )
            
            # Test failed run
            result = await main()
            assert result == 1
            
            # Verify benchmark was called
            mock_benchmark.assert_called_once()
            args, kwargs = mock_benchmark.call_args
            assert kwargs['collection_name'] == "test_collection"
            assert kwargs['num_queries'] == 5

    @pytest.mark.asyncio
    async def test_main_with_report_output(self) -> None:
        """Test main function with report output."""
        from app.ml.benchmark_quantization import main
        
        # Mock dependencies
        with patch('app.ml.benchmark_quantization.benchmark_synthetic') as mock_benchmark, \
             patch('app.ml.benchmark_quantization.AsyncQdrantClient') as mock_client_class, \
             patch('builtins.open', create=True) as mock_open:
            
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            mock_benchmark.return_value = RecallReport(
                num_queries=5,
                top_k=10,
                threshold=0.95,
                recalls=[0.96] * 5,
            )
            
            # Test with report output
            result = await main()
            assert result == 0
            
            # Verify report was written
            mock_open.assert_called_once_with("test_report.json", "w", encoding="utf-8")
            
            # Verify benchmark was called
            mock_benchmark.assert_called_once()

    @pytest.mark.asyncio
    async def test_main_client_cleanup(self) -> None:
        """Test main function cleans up Qdrant client."""
        from app.ml.benchmark_quantization import main
        
        # Mock dependencies
        mock_client = AsyncMock()
        with patch('app.ml.benchmark_quantization.AsyncQdrantClient') as mock_client_class, \
             patch('app.ml.benchmark_quantization.benchmark_synthetic') as mock_benchmark:
            
            mock_client_class.return_value = mock_client
            mock_benchmark.return_value = RecallReport(
                num_queries=5,
                top_k=10,
                threshold=0.95,
                recalls=[0.96] * 5,
            )
            
            # Test successful run
            await main()
            
            # Verify client was closed
            mock_client.close.assert_called_once()