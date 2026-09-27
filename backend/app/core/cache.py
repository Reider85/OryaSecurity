from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

from app.config import settings


@dataclass
class CacheEntry:
    verdict: str
    reason: str
    rules_matched: list[dict]
    ts: float
    expires_at: float


class DecisionCache:
    def __init__(self, ttl_seconds: int | None = None, max_size: int | None = None):
        self._ttl = ttl_seconds or settings.cache_ttl_seconds
        self._max_size = max_size or settings.cache_max_size
        self._store: dict[str, CacheEntry] = {}
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _make_key(prompt: str) -> str:
        return hashlib.sha256(prompt.encode()).hexdigest()

    def get(self, prompt: str) -> tuple[dict | None, bool]:
        key = self._make_key(prompt)
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None, False
        if time.time() > entry.expires_at:
            del self._store[key]
            self._misses += 1
            return None, False
        self._hits += 1
        return {
            "verdict": entry.verdict,
            "reason": entry.reason,
            "rules_matched": entry.rules_matched,
            "ts": entry.ts,
        }, True

    def set(self, prompt: str, verdict: str, reason: str, rules_matched: list[dict]) -> None:
        key = self._make_key(prompt)
        now = time.time()
        self._store[key] = CacheEntry(
            verdict=verdict,
            reason=reason,
            rules_matched=rules_matched,
            ts=now,
            expires_at=now + self._ttl,
        )
        if len(self._store) > self._max_size:
            oldest_key = min(self._store, key=lambda k: self._store[k].ts)
            del self._store[oldest_key]

    @property
    def hit_count(self) -> int:
        return self._hits

    @property
    def miss_count(self) -> int:
        return self._misses

    @property
    def size(self) -> int:
        return len(self._store)

    def flush(self) -> None:
        self._store.clear()
        self._hits = 0
        self._misses = 0


decision_cache = DecisionCache()
