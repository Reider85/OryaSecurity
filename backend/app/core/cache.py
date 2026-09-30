from __future__ import annotations

import hashlib
import json
import re
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

_CACHE_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


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

            # Update cache size metric
            scanner_cache_size.set(await self.count())

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
            scanner_cache_size.set(await self.count())

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

    @staticmethod
    def _is_cache_key(key: str) -> bool:
        return bool(_CACHE_KEY_RE.match(key))

    async def _cache_keys(self, client: Any) -> list[str]:
        """Collect all decision-cache keys (raw sha256 hex) via SCAN."""
        keys: list[str] = []
        cursor: Any = "0"
        while True:
            cursor, batch = await client.scan(cursor=cursor, count=1000)
            for key in batch:
                if self._is_cache_key(key):
                    keys.append(key)
            if cursor in (0, "0"):
                break
        return keys

    async def count(self) -> int:
        """Number of entries currently in the Redis-backed cache."""
        try:
            client = await get_redis_connection()
            return len(await self._cache_keys(client))
        except Exception as exc:
            logger.warning("redis_cache_count_error", error=str(exc))
            return 0

    async def list_entries(self, limit: int = 50) -> list[dict]:
        """Recent cache entries (newest first), limited to ``limit``."""
        entries: list[dict] = []
        try:
            client = await get_redis_connection()
            for key in await self._cache_keys(client):
                value = await client.get(key)
                if value is None:
                    continue
                try:
                    data = json.loads(value)
                except json.JSONDecodeError:
                    continue
                expires_at = data.get("expires_at") or 0
                if expires_at and time.time() > expires_at:
                    continue
                entries.append({
                    "prompt_hash": key,
                    "verdict": data.get("verdict", ""),
                    "ts": data.get("ts", 0),
                    "expires_at": expires_at,
                })
        except Exception as exc:
            logger.warning("redis_cache_list_error", error=str(exc))
            return []

        entries.sort(key=lambda e: e["ts"], reverse=True)
        return entries[:limit]

    async def stats(self) -> dict:
        """Cache statistics: size, hit rate, memory usage, average TTL."""
        total = 0
        memory_usage = 0
        ttl_average = 0.0
        try:
            client = await get_redis_connection()
            keys = await self._cache_keys(client)
            total = len(keys)
            ttls: list[int] = []
            for key in keys:
                value = await client.get(key)
                if value is None:
                    continue
                memory_usage += len(key.encode()) + len(value.encode())
                ttl = await client.ttl(key)
                if isinstance(ttl, int) and ttl > 0:
                    ttls.append(ttl)
            if ttls:
                ttl_average = sum(ttls) / len(ttls)
        except Exception as exc:
            logger.warning("redis_cache_stats_error", error=str(exc))

        total_lookups = self._hits + self._misses
        hit_rate = (self._hits / total_lookups * 100.0) if total_lookups else 0.0

        scanner_cache_size.set(total)
        return {
            "total_entries": total,
            "hit_rate_24h": round(hit_rate, 2),
            "memory_usage": memory_usage,
            "ttl_average": round(ttl_average, 1),
        }

    async def delete_entry(self, prompt_hash: str) -> bool:
        """Delete a single cache entry by prompt hash. Returns False if absent."""
        try:
            client = await get_redis_connection()
            deleted = await client.delete(prompt_hash)
            if deleted:
                scanner_cache_size.set(await self.count())
            return bool(deleted)
        except Exception as exc:
            logger.warning(
                "redis_cache_delete_error",
                key=prompt_hash,
                error=str(exc),
            )
            return False

    async def flush(self) -> None:
        """Delete all decision-cache entries and reset hit/miss counters."""
        try:
            client = await get_redis_connection()
            keys = await self._cache_keys(client)
            if keys:
                await client.delete(*keys)
            scanner_cache_size.set(0)
        except Exception as exc:
            logger.warning(
                "redis_cache_flush_error",
                error=str(exc),
            )

        self._hits = 0
        self._misses = 0


decision_cache = DecisionCache()