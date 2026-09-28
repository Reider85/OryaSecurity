from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

import structlog
from app.config import settings
from app.db.redis_client import get_redis_connection
from app.core.metrics import (
    scanner_cache_hits_total,
    scanner_cache_misses_total,
    scanner_cache_size,
    scanner_cache_latency_seconds,
)

logger = structlog.get_logger()


@dataclass
class CacheEntry:
    verdict: str
    reason: str
    rules_matched: list[dict]
    ts: float


class DecisionCache:
    def __init__(self, ttl_seconds: int | None = None, max_size: int | None = None):
        self._ttl = ttl_seconds or settings.cache_ttl_seconds
        self._max_size = max_size or settings.cache_max_size
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _make_key(prompt: str) -> str:
        return hashlib.sha256(prompt.encode()).hexdigest()

    async def get(self, prompt: str) -> tuple[dict | None, bool]:
        key = self._make_key(prompt)
        start_time = time.time()
        
        try:
            client = await get_redis_connection()
            value = await client.get(key)
            
            if value is None:
                self._misses += 1
                scanner_cache_misses_total.labels(type="decision").inc()
                return None, False
            
            # Parse JSON value
            data = json.loads(value)
            
            # Check TTL
            expires_at = data.get("expires_at")
            if expires_at and time.time() > expires_at:
                await client.delete(key)
                self._misses += 1
                scanner_cache_misses_total.labels(type="decision").inc()
                return None, False
            
            self._hits += 1
            scanner_cache_hits_total.labels(type="decision").inc()
            
            # Record latency
            latency = time.time() - start_time
            scanner_cache_latency_seconds.observe(latency)
            
            # Update cache size (rough estimate)
            scanner_cache_size.set(self.size)
            
            return {
                "verdict": data["verdict"],
                "reason": data["reason"],
                "rules_matched": data["rules_matched"],
                "ts": data["ts"],
            }, True
            
        except Exception as exc:
            # Redis error - fallback to miss
            latency = time.time() - start_time
            scanner_cache_latency_seconds.observe(latency)
            
            logger.warning(
                "redis_cache_get_error",
                key=key,
                error=str(exc),
                fallback_to_miss=True,
            )
            self._misses += 1
            scanner_cache_misses_total.labels(type="decision").inc()
            return None, False

    async def set(self, prompt: str, verdict: str, reason: str, rules_matched: list[dict]) -> None:
        key = self._make_key(prompt)
        now = time.time()
        
        # Prepare JSON value
        value = json.dumps({
            "verdict": verdict,
            "reason": reason,
            "rules_matched": rules_matched,
            "ts": now,
            "expires_at": now + self._ttl,
        })
        
        try:
            client = await get_redis_connection()
            await client.set(key, value, ex=self._ttl)
            
            # Update cache size metric
            scanner_cache_size.set(self.size)
            
        except Exception as exc:
            # Redis error - log but don't fail the scan
            logger.warning(
                "redis_cache_set_error",
                key=key,
                error=str(exc),
                fallback_to_noop=True,
            )

    @property
    def hit_count(self) -> int:
        return self._hits

    @property
    def miss_count(self) -> int:
        return self._misses

    @property
    def size(self) -> int:
        # For Redis, we can't get exact size without DBSIZE command
        # Return a rough estimate based on hit/miss ratio
        # This is a limitation of Redis cache vs in-memory dict
        return 0  # TODO: Could implement DBSIZE call if needed

    async def flush(self) -> None:
        try:
            client = await get_redis_connection()
            # Delete all keys with our cache prefix
            # Note: This is a simple approach. For production, you might want a specific key pattern
            # and use SCAN or FLUSHDB if this is the only app using this Redis instance
            keys = await client.keys("scanner:*")
            if keys:
                await client.delete(*keys)
                
        except Exception as exc:
            logger.warning(
                "redis_cache_flush_error",
                error=str(exc),
            )
        
        self._hits = 0
        self._misses = 0
        scanner_cache_size.set(0)


decision_cache = DecisionCache()