from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import redis.asyncio as redis
import structlog
from prometheus_client import Counter, Gauge, Histogram

from app.config import settings
from app.db.redis_client import get_redis_bytes
from app.core.metrics import (
    scanner_embedding_cache_hits_total,
    scanner_embedding_cache_lookups_total,
    scanner_embedding_cache_size,
)

logger = structlog.get_logger(__name__)

# Constants
_EMBED_KEY_PREFIX = "emb:"
_EMBEDDING_DIM = settings.embedding_dim
_EMBEDDING_BYTES = _EMBEDDING_DIM * 4  # FP32 = 4 bytes per dimension
_LRU_ZSET_KEY = "emb:lru"

# Metrics
_hits = Counter("embedding_cache_hits_total", "Total embedding cache hits")
_misses = Counter("embedding_cache_misses_total", "Total embedding cache misses")
_latency = Histogram("embedding_cache_latency_seconds", "Cache operation latency")


@dataclass
class EmbeddingCacheStats:
    """Statistics for embedding cache."""
    hits: int = 0
    misses: int = 0
    total_entries: int = 0
    avg_ttl: float = 0.0
    memory_usage: int = 0


class EmbeddingCache:
    """Redis-based embedding cache with LRU eviction."""

    def __init__(
        self,
        ttl_seconds: int | None = None,
        max_entries: int | None = None,
    ):
        self._ttl = ttl_seconds or settings.embedding_cache_ttl_seconds
        self._max_entries = max_entries or settings.embedding_cache_max_entries
        self._hits = 0
        self._misses = 0
        self._total_entries = 0
        self._ttl_sum = 0.0
        self._ttl_count = 0

    @staticmethod
    def _make_key(text: str) -> str:
        """Generate cache key for text."""
        return f"{_EMBED_KEY_PREFIX}{hashlib.sha256(text.encode()).hexdigest()}"

    async def get(self, text: str) -> Optional[np.ndarray]:
        """Get embedding from cache.
        
        Args:
            text: Input text to get embedding for
            
        Returns:
            Embedding as numpy array (shape: (_EMBEDDING_DIM,)) or None if miss
        """
        start_time = time.time()
        key = self._make_key(text)
        
        try:
            client = await get_redis_bytes()
            scanner_embedding_cache_lookups_total.inc()
            
            # Check if key exists
            if not await client.exists(key):
                self._misses += 1
                _misses.inc()
                return None
            
            # Get embedding data
            data = await client.get(key)
            if data is None:
                self._misses += 1
                _misses.inc()
                return None
            
            # Update LRU timestamp
            await client.zadd(_LRU_ZSET_KEY, {key: time.time()})
            
            # Convert bytes to numpy array
            embedding = np.frombuffer(data, dtype=np.float32)
            if embedding.shape != (_EMBEDDING_DIM,):
                logger.warning("embedding_cache_shape_mismatch", key=key, expected=_EMBEDDING_DIM, actual=embedding.shape)
                return None
            
            self._hits += 1
            _hits.inc()
            scanner_embedding_cache_hits_total.inc()
            
            _latency.observe(time.time() - start_time)
            return embedding
            
        except Exception as e:
            logger.warning("embedding_cache_get_error", key=key, error=str(e), fallback_to_miss=True)
            self._misses += 1
            _misses.inc()
            return None

    async def set(self, text: str, embedding: np.ndarray) -> None:
        """Store embedding in cache.
        
        Args:
            text: Input text
            embedding: Embedding as numpy array (shape: (_EMBEDDING_DIM,))
        """
        start_time = time.time()
        key = self._make_key(text)
        
        if embedding.shape != (_EMBEDDING_DIM,):
            raise ValueError(f"Embedding must have shape ({_EMBEDDING_DIM},), got {embedding.shape}")
        
        try:
            client = await get_redis_bytes()
            
            # Convert to bytes
            embedding_bytes = embedding.astype(np.float32).tobytes()
            
            # Store with TTL
            await client.setex(key, self._ttl, embedding_bytes)
            
            # Add to LRU set
            await client.zadd(_LRU_ZSET_KEY, {key: time.time()})
            
            # Check and perform LRU eviction if needed
            await self._evict_if_needed(client)
            
            self._total_entries += 1
            self._ttl_sum += self._ttl
            self._ttl_count += 1
            
            _latency.observe(time.time() - start_time)
            
        except Exception as e:
            logger.warning("embedding_cache_set_error", key=key, error=str(e))

    async def _evict_if_needed(self, client: redis.Redis) -> None:
        """Evict oldest entries if cache exceeds max size."""
        current_size = await client.zcard(_LRU_ZSET_KEY)
        if current_size <= self._max_entries:
            return
        
        # Evict oldest entries
        to_evict = current_size - self._max_entries
        oldest_keys = await client.zpopmin(_LRU_ZSET_KEY, to_evict)
        
        for key, _ in oldest_keys:
            await client.delete(key)
        
        logger.info("embedding_cache_evicted", count=to_evict)

    async def stats(self) -> EmbeddingCacheStats:
        """Get cache statistics.
        
        Returns:
            Cache statistics
        """
        try:
            client = await get_redis_bytes()
            total_entries = await client.zcard(_LRU_ZSET_KEY)
            avg_ttl = self._ttl_sum / self._ttl_count if self._ttl_count > 0 else 0.0
            
            # Estimate memory usage (rough approximation)
            memory_usage = total_entries * (_EMBEDDING_BYTES + 64)  # 64 bytes overhead per key
            
            scanner_embedding_cache_size.set(total_entries)
            
            return EmbeddingCacheStats(
                hits=self._hits,
                misses=self._misses,
                total_entries=total_entries,
                avg_ttl=avg_ttl,
                memory_usage=memory_usage,
            )
            
        except Exception as e:
            logger.warning("embedding_cache_stats_error", error=str(e))
            return EmbeddingCacheStats(
                hits=self._hits,
                misses=self._misses,
                total_entries=0,
                avg_ttl=0.0,
                memory_usage=0,
            )


# Global singleton instance
embedding_cache = EmbeddingCache()