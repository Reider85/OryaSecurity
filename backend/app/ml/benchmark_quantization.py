"""Benchmark recall@10 for scalar int8 quantization in Qdrant.

Compares vector search results between baseline (full precision) and quantized
int8 collections. Target: recall@10 > 95% (mean overlap of top-10 results).

Usage:
    # Synthetic benchmark (no ML dependencies)
    python -m app.ml.benchmark_quantization --synthetic --num-points 2000 --num-queries 50

    # Benchmark using default prompts (requires ML models)
    python -m app.ml.benchmark_quantization --synthetic --num-points 1000 --num-queries 100

    # Benchmark using existing collection as corpus
    python -m app.ml.benchmark_quantization --corpus prompt_attacks --queries 50

    # Report output
    python -m app.ml.benchmark_quantization --synthetic --report benchmark_report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence

import numpy as np
import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from app.config import settings

logger = structlog.get_logger("benchmark_quantization")

DEFAULT_TOP_K = 10
DEFAULT_THRESHOLD = 0.95
DEFAULT_DIM = 1024


@dataclass
class RecallReport:
    """Benchmark results for recall@10."""
    num_queries: int
    top_k: int
    threshold: float
    recalls: List[float] = field(default_factory=list)
    
    @property
    def mean_recall(self) -> float:
        """Mean recall@k across all queries."""
        return float(np.mean(self.recalls)) if self.recalls else 0.0
    
    @property
    def min_recall(self) -> float:
        """Minimum recall@k across all queries."""
        return float(np.min(self.recalls)) if self.recalls else 0.0
    
    @property
    def p05_recall(self) -> float:
        """5th percentile recall@k."""
        if not self.recalls:
            return 0.0
        return float(np.percentile(self.recalls, 5))
    
    @property
    def passed(self) -> bool:
        """Whether mean recall meets threshold."""
        return self.num_queries > 0 and self.mean_recall >= self.threshold
    
    def to_dict(self, include_per_query: bool = False) -> dict:
        """Convert to dictionary for JSON serialization."""
        data: dict = {
            "num_queries": self.num_queries,
            "top_k": self.top_k,
            "threshold": self.threshold,
            "mean_recall": self.mean_recall,
            "min_recall": self.min_recall,
            "p05_recall": self.p05_recall,
            "passed": self.passed,
        }
        if include_per_query:
            data["recalls"] = self.recalls
        return data


def compute_recall_at_k(baseline_ids: List[str], quantized_ids: List[str], k: int = 10) -> float:
    """Compute recall@k between two ranked lists of IDs.
    
    Args:
        baseline_ids: Ground truth top-k IDs from full-precision search
        quantized_ids: Top-k IDs from quantized search
        k: Number of results to consider
        
    Returns:
        Recall@k = |intersection| / k
    """
    baseline_set = set(baseline_ids[:k])
    quantized_set = set(quantized_ids[:k])
    intersection = baseline_set.intersection(quantized_set)
    return len(intersection) / k


async def generate_synthetic_corpus(
    num_points: int,
    dim: int = DEFAULT_DIM,
    num_clusters: int = 10,
    seed: int = 42,
) -> List[np.ndarray]:
    """Generate synthetic corpus with clustered vectors for realistic benchmark.
    
    Args:
        num_points: Number of vectors to generate
        dim: Vector dimension
        num_clusters: Number of Gaussian clusters
        seed: Random seed for reproducibility
        
    Returns:
        List of L2-normalized vectors
    """
    rng = np.random.RandomState(seed)
    
    # Generate cluster centers
    cluster_centers = rng.randn(num_clusters, dim)
    cluster_centers = cluster_centers / np.linalg.norm(cluster_centers, axis=1, keepdims=True)
    
    # Generate points near cluster centers
    vectors = []
    for _ in range(num_points):
        # Choose random cluster
        cluster_idx = rng.randint(0, num_clusters)
        
        # Generate point near cluster center with noise
        noise = rng.randn(dim) * 0.1  # Small noise
        vector = cluster_centers[cluster_idx] + noise
        
        # L2 normalize
        vector = vector / np.linalg.norm(vector)
        vectors.append(vector.astype(np.float32))
    
    return vectors


async def generate_synthetic_queries(
    num_queries: int,
    corpus: List[np.ndarray],
    dim: int = DEFAULT_DIM,
    seed: int = 42,
) -> List[np.ndarray]:
    """Generate synthetic query vectors based on corpus.
    
    Args:
        num_queries: Number of query vectors to generate
        corpus: Existing corpus vectors
        dim: Vector dimension
        seed: Random seed for reproducibility
        
    Returns:
        List of L2-normalized query vectors
    """
    rng = np.random.RandomState(seed)
    
    # Sample random points from corpus and add small noise
    query_indices = rng.choice(len(corpus), size=num_queries, replace=True)
    queries = []
    
    for idx in query_indices:
        base_vector = corpus[idx]
        noise = rng.randn(dim) * 0.05  # Smaller noise for queries
        query = base_vector + noise
        query = query / np.linalg.norm(query)
        queries.append(query.astype(np.float32))
    
    return queries


async def create_test_collection(
    client: AsyncQdrantClient,
    name: str,
    vectors: List[np.ndarray],
    quantized: bool = True,
) -> None:
    """Create test collection with or without quantization."""
    # Create collection
    await client.create_collection(
        collection_name=name,
        vectors_config=models.VectorParams(size=vectors[0].shape[0], distance="Cosine"),
        hnsw_config=models.HnswConfigDiff(m=16, ef_construct=128),
        quantization_config=(
            models.QuantizationConfig(
                scalar={
                    "type": models.ScalarType.INT8,
                    "quantile": 0.99,
                    "always_ram": True,
                }
            ) if quantized else None
        ),
    )
    
    # Upsert vectors
    points = [
        models.PointStruct(
            id=i,
            vector=vector.tolist(),
            payload={"id": f"point_{i}"},
        )
        for i, vector in enumerate(vectors)
    ]
    
    # Upsert in batches
    batch_size = 256
    for i in range(0, len(points), batch_size):
        batch = points[i:i + batch_size]
        await client.upsert(
            collection_name=name,
            points=batch,
            wait=True,
        )
    
    logger.info("test_collection_created", name=name, count=len(vectors), quantized=quantized)


async def search_collection(
    client: AsyncQdrantClient,
    collection_name: str,
    query_vector: np.ndarray,
    top_k: int = DEFAULT_TOP_K,
    quantized: bool = True,
) -> List[str]:
    """Search collection with or without quantization.
    
    Args:
        client: Qdrant client
        collection_name: Collection to search
        query_vector: Query vector
        top_k: Number of results to return
        quantized: Whether to use quantized search (with rescore)
        
    Returns:
        List of result IDs
    """
    search_params = None
    if quantized:
        search_params = models.SearchParams(
            quantization=models.QuantizationSearchParams(
                ignore=False,
                rescore=True,
                oversampling=1.0,
            )
        )
    
    response = await client.query_points(
        collection_name=collection_name,
        query=query_vector.tolist(),
        limit=top_k,
        search_params=search_params,
        with_payload=["id"],
    )
    
    return [point.payload["id"] for point in response.points]


async def run_benchmark(
    client: AsyncQdrantClient,
    corpus_vectors: List[np.ndarray],
    query_vectors: List[np.ndarray],
    top_k: int = DEFAULT_TOP_K,
    threshold: float = DEFAULT_THRESHOLD,
    collection_prefix: str = "benchmark",
) -> RecallReport:
    """Run recall@10 benchmark comparing baseline vs quantized search.
    
    Args:
        client: Qdrant client
        corpus_vectors: Corpus vectors to index
        query_vectors: Query vectors for benchmark
        top_k: Number of results to consider
        threshold: Minimum mean recall threshold
        collection_prefix: Prefix for temporary collection names
        
    Returns:
        RecallReport with results
    """
    baseline_name = f"{collection_prefix}_baseline"
    quantized_name = f"{collection_prefix}_quantized"
    
    try:
        # Create test collections
        logger.info("creating_test_collections", corpus_size=len(corpus_vectors), query_size=len(query_vectors))
        await create_test_collection(client, baseline_name, corpus_vectors, quantized=False)
        await create_test_collection(client, quantized_name, corpus_vectors, quantized=True)
        
        # Run benchmark
        recalls = []
        for i, query_vector in enumerate(query_vectors):
            logger.debug("running_query", query_id=i, total=len(query_vectors))
            
            # Search baseline (full precision)
            baseline_ids = await search_collection(
                client, baseline_name, query_vector, top_k, quantized=False
            )
            
            # Search quantized
            quantized_ids = await search_collection(
                client, quantized_name, query_vector, top_k, quantized=True
            )
            
            # Compute recall
            recall = compute_recall_at_k(baseline_ids, quantized_ids, top_k)
            recalls.append(recall)
            
            if (i + 1) % 10 == 0:
                logger.info("benchmark_progress", completed=i + 1, total=len(query_vectors), mean_recall=np.mean(recalls))
        
        # Generate report
        report = RecallReport(
            num_queries=len(query_vectors),
            top_k=top_k,
            threshold=threshold,
            recalls=recalls,
        )
        
        logger.info("benchmark_completed", report=report.to_dict())
        return report
        
    finally:
        # Clean up test collections
        try:
            await client.delete_collection(baseline_name)
            await client.delete_collection(quantized_name)
            logger.info("collections_cleaned", baseline=baseline_name, quantized=quantized_name)
        except Exception as e:
            logger.warning("cleanup_failed", error=str(e))


async def benchmark_synthetic(
    client: AsyncQdrantClient,
    num_points: int,
    num_queries: int,
    dim: int = DEFAULT_DIM,
    top_k: int = DEFAULT_TOP_K,
    threshold: float = DEFAULT_THRESHOLD,
    seed: int = 42,
) -> RecallReport:
    """Run synthetic benchmark with generated vectors.
    
    Args:
        client: Qdrant client
        num_points: Number of corpus vectors
        num_queries: Number of query vectors
        dim: Vector dimension
        top_k: Number of results to consider
        threshold: Minimum mean recall threshold
        seed: Random seed for reproducibility
        
    Returns:
        RecallReport with results
    """
    logger.info("starting_synthetic_benchmark", num_points=num_points, num_queries=num_queries, dim=dim)
    
    # Generate corpus and queries
    corpus = await generate_synthetic_corpus(num_points, dim, seed=seed)
    queries = await generate_synthetic_queries(num_queries, corpus, dim, seed)
    
    return await run_benchmark(
        client,
        corpus,
        queries,
        top_k=top_k,
        threshold=threshold,
        collection_prefix=f"synthetic_{dim}d_{num_points}p",
    )


async def benchmark_from_collection(
    client: AsyncQdrantClient,
    collection_name: str,
    num_queries: int,
    top_k: int = DEFAULT_TOP_K,
    threshold: float = DEFAULT_THRESHOLD,
) -> RecallReport:
    """Run benchmark using existing collection as corpus.
    
    Args:
        client: Qdrant client
        collection_name: Existing collection to use as corpus
        num_queries: Number of query vectors to generate
        top_k: Number of results to consider
        threshold: Minimum mean recall threshold
        
    Returns:
        RecallReport with results
    """
    logger.info("starting_collection_benchmark", collection=collection_name, num_queries=num_queries)
    
    # Get all vectors from collection
    all_vectors = []
    all_ids = []
    
    offset = None
    while True:
        response = await client.scroll(
            collection_name=collection_name,
            limit=1000,
            offset=offset,
            with_payload=["id"],
            with_vectors=True,
        )
        
        if not response[0]:
            break
        
        for point in response[0]:
            all_vectors.append(np.array(point.vector, dtype=np.float32))
            all_ids.append(point.payload["id"])
        
        offset = response[1]
    
    logger.info("collection_loaded", count=len(all_vectors))
    
    # Generate queries from corpus
    queries = await generate_synthetic_queries(num_queries, all_vectors)
    
    return await run_benchmark(
        client,
        all_vectors,
        queries,
        top_k=top_k,
        threshold=threshold,
        collection_prefix=f"from_{collection_name}",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build argument parser for CLI."""
    parser = argparse.ArgumentParser(
        description="Benchmark recall@10 for scalar int8 quantization"
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Run synthetic benchmark with generated vectors"
    )
    parser.add_argument(
        "--corpus",
        help="Use existing collection as corpus (alternative to --synthetic)"
    )
    parser.add_argument(
        "--num-points",
        type=int,
        default=1000,
        help="Number of corpus vectors (synthetic mode, default: 1000)"
    )
    parser.add_argument(
        "--num-queries",
        type=int,
        default=50,
        help="Number of query vectors (default: 50)"
    )
    parser.add_argument(
        "--dim",
        type=int,
        default=DEFAULT_DIM,
        help="Vector dimension (default: 1024)"
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help="Number of results to consider (default: 10)"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="Minimum mean recall threshold (default: 0.95)"
    )
    parser.add_argument(
        "--report",
        help="Path to write JSON report"
    )
    parser.add_argument(
        "--per-query",
        action="store_true",
        help="Include per-query recalls in report"
    )
    parser.add_argument(
        "--url",
        default=settings.qdrant_url,
        help=f"Qdrant URL (default: {settings.qdrant_url})"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level"
    )
    return parser


async def main() -> int:
    """Main CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    
    # Validate arguments
    if not args.synthetic and not args.corpus:
        parser.error("Must specify either --synthetic or --corpus")
    if args.synthetic and args.corpus:
        parser.error("Cannot specify both --synthetic and --corpus")
    
    # Create Qdrant client
    client = AsyncQdrantClient(url=args.url, timeout=2.0)
    
    try:
        # Run benchmark
        if args.synthetic:
            report = await benchmark_synthetic(
                client,
                num_points=args.num_points,
                num_queries=args.num_queries,
                dim=args.dim,
                top_k=args.top_k,
                threshold=args.threshold,
            )
        else:
            report = await benchmark_from_collection(
                client,
                args.corpus,
                num_queries=args.num_queries,
                top_k=args.top_k,
                threshold=args.threshold,
            )
        
        # Output results
        summary = report.to_dict(include_per_query=args.per_query)
        print(json.dumps(summary, indent=2))
        
        if args.report:
            with open(args.report, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2)
            logger.info("report_written", path=args.report)
        
        if not report.passed:
            print(
                f"BENCHMARK FAILED: mean recall {report.mean_recall:.4f} < threshold {report.threshold}",
                file=logging.stderr,
            )
            return 1
        
        print(f"BENCHMARK PASSED: mean recall {report.mean_recall:.4f} >= {report.threshold}", file=logging.stderr)
        return 0
        
    except Exception as e:
        logger.error("benchmark_failed", error=str(e))
        return 1
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))