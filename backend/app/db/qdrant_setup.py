"""Canonical Qdrant collection setup with scalar quantization.

Provides idempotent collection creation, verification, and patching
for Qdrant vector collections with HNSW + optional scalar int8 quantization.

Usage:
    # Ensure collection exists with correct config
    from app.db.qdrant_setup import ensure_collection
    await ensure_collection(client, "prompt_attacks", quantized=True)

    # Create new collection
    from app.db.qdrant_setup import create_collection
    await create_collection(client, "test_collection", size=1024, quantized=True)

    # Add quantization to existing collection
    from app.db.qdrant_setup import enable_scalar_quantization
    await enable_scalar_quantization(client, "existing_collection")

    # Verify collection configuration
    from app.db.qdrant_setup import verify_collection_config
    config = await verify_collection_config(client, "prompt_attacks")
    assert config.quantization_config.scalar.type == "int8"

    # CLI usage:
    # python -m app.db.qdrant_setup --collection prompt_attacks --force
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Optional

import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from app.config import settings

logger = structlog.get_logger("qdrant_setup")

# Constants matching EP-09 & prompt 14
HNSW_M = 16
HNSW_EF_CONSTRUCT = 128
QUANTILE = 0.99
ALWAYS_RAM = True
DEFAULT_DISTANCE = "Cosine"


def build_quantization_config(
    quantile: float = QUANTILE,
    always_ram: bool = ALWAYS_RAM,
) -> models.QuantizationConfig:
    """Build scalar quantization config for Qdrant."""
    return models.QuantizationConfig(
        scalar={
            "type": models.ScalarType.INT8,
            "quantile": quantile,
            "always_ram": always_ram,
        }
    )


def build_hnsw_config(
    m: int = HNSW_M,
    ef_construct: int = HNSW_EF_CONSTRUCT,
) -> models.HnswConfigDiff:
    """Build HNSW config for Qdrant collection."""
    return models.HnswConfigDiff(m=m, ef_construct=ef_construct)


async def create_collection(
    client: AsyncQdrantClient,
    collection_name: str,
    size: Optional[int] = None,
    quantized: bool = True,
    distance: str = DEFAULT_DISTANCE,
) -> None:
    """Create collection with HNSW + optional scalar int8 quantization.
    
    Args:
        client: Qdrant client instance
        collection_name: Name of collection to create
        size: Vector dimension (default: settings.embedding_dim)
        quantized: Whether to enable scalar int8 quantization
        distance: Vector distance metric (default: "Cosine")
    """
    size = size or settings.embedding_dim
    
    collection_config = {
        "collection_name": collection_name,
        "vectors_config": models.VectorParams(size=size, distance=distance),
        "hnsw_config": build_hnsw_config(),
    }
    
    if quantized:
        collection_config["quantization_config"] = build_quantization_config()
    
    await client.create_collection(**collection_config)
    logger.info("collection_created", name=collection_name, size=size, quantized=quantized)


async def enable_scalar_quantization(
    client: AsyncQdrantClient,
    collection_name: str,
    quantile: float = QUANTILE,
    always_ram: bool = ALWAYS_RAM,
) -> None:
    """Enable scalar quantization on existing collection.
    
    Args:
        client: Qdrant client instance
        collection_name: Name of existing collection
        quantile: Quantile for quantization
        always_ram: Whether to keep original vectors in RAM
    """
    quantization_config = build_quantization_config(quantile, always_ram)
    
    await client.update_collection(
        collection_name=collection_name,
        quantization_config=quantization_config,
    )
    logger.info("quantization_enabled", name=collection_name, quantile=quantile, always_ram=always_ram)


async def verify_collection_config(
    client: AsyncQdrantClient,
    collection_name: str,
    size: Optional[int] = None,
    distance: str = DEFAULT_DISTANCE,
    quantized: bool = True,
) -> models.CollectionConfig:
    """Verify collection configuration matches expected values.
    
    Args:
        client: Qdrant client instance
        collection_name: Name of collection to verify
        size: Expected vector dimension
        distance: Expected distance metric
        quantized: Whether quantization should be present
        
    Returns:
        Collection configuration from Qdrant
        
    Raises:
        AssertionError: If configuration does not match expected values
    """
    size = size or settings.embedding_dim
    
    collection_info = await client.get_collection(collection_name)
    config = collection_info.config
    
    # Verify vector parameters
    assert config.params.vectors.size == size, f"Vector size mismatch: {config.params.vectors.size} != {size}"
    assert config.params.vectors.distance == distance, f"Distance mismatch: {config.params.vectors.distance} != {distance}"
    
    # Verify HNSW parameters
    assert config.hnsw_config.m == HNSW_M, f"HNSW m mismatch: {config.hnsw_config.m} != {HNSW_M}"
    assert config.hnsw_config.ef_construct == HNSW_EF_CONSTRUCT, f"HNSW ef_construct mismatch: {config.hnsw_config.ef_construct} != {HNSW_EF_CONSTRUCT}"
    
    # Verify quantization parameters
    if quantized:
        assert config.quantization_config is not None, "Quantization config missing"
        assert config.quantization_config.scalar.type == "int8", f"Quantization type mismatch: {config.quantization_config.scalar.type}"
        assert config.quantization_config.scalar.quantile == QUANTILE, f"Quantile mismatch: {config.quantization_config.scalar.quantile} != {QUANTILE}"
        assert config.quantization_config.scalar.always_ram == ALWAYS_RAM, f"Always RAM mismatch: {config.quantization_config.scalar.always_ram} != {ALWAYS_RAM}"
    else:
        assert config.quantization_config is None, "Quantization config present but not expected"
    
    logger.info("collection_config_verified", name=collection_name, size=size, quantized=quantized)
    return config


async def ensure_collection(
    client: AsyncQdrantClient,
    collection_name: str,
    force_recreate: bool = False,
    size: Optional[int] = None,
    quantized: bool = True,
    distance: str = DEFAULT_DISTANCE,
) -> None:
    """Ensure collection exists with correct configuration.
    
    If collection exists with mismatched config, recreates it.
    If collection doesn't exist, creates it.
    
    Args:
        client: Qdrant client instance
        collection_name: Name of collection to ensure
        force_recreate: Always recreate if collection exists
        size: Expected vector dimension
        quantized: Whether to enable scalar int8 quantization
        distance: Expected distance metric
    """
    size = size or settings.embedding_dim
    
    try:
        collection_info = await client.get_collection(collection_name)
        logger.info("collection_exists", name=collection_name, points=collection_info.points_count)
        
        if force_recreate:
            logger.info("recreating_collection", name=collection_name)
            await client.delete_collection(collection_name)
            await create_collection(client, collection_name, size, quantized, distance)
        else:
            # Check if configuration matches
            config = collection_info.config
            config_mismatch = (
                config.params.vectors.size != size or
                config.params.vectors.distance != distance or
                config.hnsw_config.m != HNSW_M or
                config.hnsw_config.ef_construct != HNSW_EF_CONSTRUCT or
                quantized != (config.quantization_config is not None)
            )
            
            if quantized and config.quantization_config:
                # Check quantization details
                q_config = config.quantization_config.scalar
                config_mismatch = (
                    config_mismatch or
                    q_config.type != "int8" or
                    q_config.quantile != QUANTILE or
                    q_config.always_ram != ALWAYS_RAM
                )
            
            if config_mismatch:
                logger.warning(
                    "collection_config_mismatch",
                    name=collection_name,
                    existing_size=config.params.vectors.size,
                    expected_size=size,
                    existing_distance=config.params.vectors.distance,
                    expected_distance=distance,
                    existing_m=config.hnsw_config.m,
                    expected_m=HNSW_M,
                    existing_ef_construct=config.hnsw_config.ef_construct,
                    expected_ef_construct=HNSW_EF_CONSTRUCT,
                    has_quantization=bool(config.quantization_config),
                    quantization_type=config.quantization_config.scalar.type if config.quantization_config else None,
                    force_recreate=force_recreate,
                )
                logger.info("forcing_collection_recreation", name=collection_name)
                await client.delete_collection(collection_name)
                await create_collection(client, collection_name, size, quantized, distance)
            else:
                logger.info("collection_config_correct", name=collection_name)
        
    except Exception as e:
        if "not found" in str(e).lower():
            logger.info("creating_collection", name=collection_name)
            await create_collection(client, collection_name, size, quantized, distance)
        else:
            raise
    
    await verify_collection_config(client, collection_name, size, distance, quantized)


def build_parser() -> argparse.ArgumentParser:
    """Build argument parser for CLI."""
    parser = argparse.ArgumentParser(
        description="Setup Qdrant collection with scalar quantization"
    )
    parser.add_argument(
        "--collection",
        default="prompt_attacks",
        help="Collection name (default: prompt_attacks)"
    )
    parser.add_argument(
        "--size",
        type=int,
        default=settings.embedding_dim,
        help=f"Vector dimension (default: {settings.embedding_dim})"
    )
    parser.add_argument(
        "--distance",
        default=DEFAULT_DISTANCE,
        help=f"Distance metric (default: {DEFAULT_DISTANCE})"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force recreation if collection exists"
    )
    parser.add_argument(
        "--no-quantization",
        action="store_true",
        help="Create collection without quantization"
    )
    parser.add_argument(
        "--enable-quantization-only",
        action="store_true",
        help="Only enable quantization on existing collection (do not create)"
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify existing collection configuration"
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
    
    # Create Qdrant client
    client = AsyncQdrantClient(url=args.url, timeout=2.0)
    
    try:
        if args.enable_quantization_only:
            await enable_scalar_quantization(client, args.collection)
        elif args.verify_only:
            await verify_collection_config(client, args.collection, args.size, args.distance, not args.no_quantization)
        else:
            await ensure_collection(
                client,
                args.collection,
                force_recreate=args.force,
                size=args.size,
                quantized=not args.no_quantization,
                distance=args.distance,
            )
        
        logger.info("qdrant_setup_completed", collection=args.collection)
        return 0
        
    except Exception as e:
        logger.error("qdrant_setup_failed", error=str(e), collection=args.collection)
        return 1
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))