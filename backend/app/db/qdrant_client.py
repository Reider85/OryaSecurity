from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.config import settings

# Global Qdrant client instance
_client: AsyncQdrantClient | None = None


async def get_qdrant() -> AsyncQdrantClient:
    """Get the global Qdrant client. Initialize if not exists."""
    global _client
    if _client is None:
        _client = AsyncQdrantClient(
            url=settings.qdrant_url,
            timeout=2.0,
            prefer_grpc=False,
        )
    return _client


async def init_qdrant() -> None:
    """Initialize Qdrant connection and test connectivity."""
    try:
        client = await get_qdrant()
        # Test the connection by getting cluster info
        info = await client.get_cluster_info()
        if not info:
            raise ConnectionError("Qdrant cluster info is empty")
    except Exception as e:
        # Non-fatal initialization: log warning and continue
        # VectorSearcher will degrade gracefully if Qdrant is unavailable
        from app.core.logging import get_logger
        logger = get_logger(__name__)
        logger.warning(
            "Qdrant connection check failed, scanner will operate in degraded mode",
            error=str(e),
            qdrant_url=settings.qdrant_url,
        )


async def close_qdrant() -> None:
    """Close the Qdrant connection."""
    global _client
    if _client:
        await _client.close()
        _client = None


async def get_qdrant_connection() -> AsyncQdrantClient:
    """Get a Qdrant connection (alias for get_qdrant for consistency)."""
    return await get_qdrant()


async def get_cluster_info() -> Optional[models.ClusterInfo]:
    """Get Qdrant cluster info for monitoring."""
    try:
        client = await get_qdrant()
        return await client.get_cluster_info()
    except Exception:
        return None


async def get_collection_info(collection_name: str) -> Optional[models.CollectionInfo]:
    """Get collection info for monitoring."""
    try:
        client = await get_qdrant()
        return await client.get_collection(collection_name)
    except Exception:
        return None