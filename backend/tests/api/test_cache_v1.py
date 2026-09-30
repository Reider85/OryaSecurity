from __future__ import annotations

import hashlib
import json
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.core.auth import AuthInfo, verify_api_key
from app.core.cache import DecisionCache, decision_cache


class InMemoryRedis:
    """Minimal loop-agnostic Redis stand-in for cache API tests."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                self.ttls.pop(key, None)
                deleted += 1
        return deleted

    async def scan(self, cursor: str = "0", count: int = 1000) -> tuple[str, list[str]]:
        return "0", list(self.store.keys())

    async def ttl(self, key: str) -> int:
        return self.ttls.get(key, -1)

    async def keys(self, pattern: str = "*") -> list[str]:
        return list(self.store.keys())


@pytest.fixture
def auth_override():
    """Override API-key auth so cache API tests do not require Postgres."""
    def _override() -> AuthInfo:
        return AuthInfo(api_key="test-key", tenant_id="test-tenant")

    app.dependency_overrides[verify_api_key] = _override
    yield
    app.dependency_overrides.pop(verify_api_key, None)


@pytest.fixture
def unauthorized_override():
    def _override() -> AuthInfo:
        raise HTTPException(status_code=401, detail="Invalid API key")

    app.dependency_overrides[verify_api_key] = _override
    yield
    app.dependency_overrides.pop(verify_api_key, None)


@pytest.fixture
async def cache_store(monkeypatch: pytest.MonkeyPatch):
    """Point DecisionCache at an in-memory Redis double."""
    store = InMemoryRedis()

    async def _get_redis():
        return store

    monkeypatch.setattr("app.core.cache.get_redis_connection", _get_redis)

    saved_hits = decision_cache._hits
    saved_misses = decision_cache._misses
    decision_cache._hits = 0
    decision_cache._misses = 0
    yield store
    decision_cache._hits = saved_hits
    decision_cache._misses = saved_misses


async def _seed_entry(
    store: InMemoryRedis,
    prompt: str,
    verdict: str = "allow",
    reason: str = "clean",
    ttl: int = 300,
    ts: float | None = None,
) -> str:
    key = hashlib.sha256(prompt.encode()).hexdigest()
    now = ts if ts is not None else time.time()
    value = json.dumps({
        "verdict": verdict,
        "reason": reason,
        "rules_matched": [],
        "ts": now,
        "expires_at": now + ttl,
    })
    await store.set(key, value, ex=ttl)
    return key


class TestCacheStats:
    async def test_stats_empty_cache(self, client: TestClient, auth_override, cache_store):
        resp = client.get("/api/v1/cache/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_entries"] == 0
        assert data["hit_rate_24h"] == 0.0
        assert data["memory_usage"] == 0
        assert data["ttl_average"] == 0.0

    async def test_stats_with_entries(
        self, client: TestClient, auth_override, cache_store
    ):
        await _seed_entry(cache_store, "prompt one", ttl=300)
        await _seed_entry(cache_store, "prompt two", verdict="block", reason="pii", ttl=600)

        resp = client.get("/api/v1/cache/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_entries"] == 2
        assert data["memory_usage"] > 0
        assert data["ttl_average"] == 450.0

    async def test_stats_hit_rate_from_counters(
        self, client: TestClient, auth_override, cache_store
    ):
        decision_cache._hits = 3
        decision_cache._misses = 1

        resp = client.get("/api/v1/cache/stats")
        assert resp.status_code == 200
        assert resp.json()["hit_rate_24h"] == 75.0

    async def test_stats_unauthorized(self, client: TestClient, unauthorized_override):
        resp = client.get("/api/v1/cache/stats")
        assert resp.status_code == 401


class TestListCacheEntries:
    async def test_list_returns_entries(self, client: TestClient, auth_override, cache_store):
        await _seed_entry(cache_store, "prompt one", ttl=300)
        await _seed_entry(cache_store, "prompt two", verdict="block", ttl=300)

        resp = client.get("/api/v1/cache")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        by_hash = {e["prompt_hash"]: e for e in data}
        assert set(by_hash) == {
            hashlib.sha256(b"prompt one").hexdigest(),
            hashlib.sha256(b"prompt two").hexdigest(),
        }
        entry = by_hash[hashlib.sha256(b"prompt one").hexdigest()]
        assert entry["verdict"] == "allow"
        assert "T" in entry["ts"]  # ISO datetime
        assert "T" in entry["expires_at"]

    async def test_list_sorted_newest_first(
        self, client: TestClient, auth_override, cache_store
    ):
        older = time.time() - 100
        newer = time.time()
        key_older = await _seed_entry(cache_store, "old", ts=older)
        key_newer = await _seed_entry(cache_store, "new", ts=newer)

        resp = client.get("/api/v1/cache")
        data = resp.json()
        assert data[0]["prompt_hash"] == key_newer
        assert data[1]["prompt_hash"] == key_older

    async def test_list_respects_limit(self, client: TestClient, auth_override, cache_store):
        for i in range(10):
            await _seed_entry(cache_store, f"prompt {i}", ts=time.time() + i)

        resp = client.get("/api/v1/cache?limit=3")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    async def test_list_rejects_invalid_limit(self, client: TestClient, auth_override, cache_store):
        resp = client.get("/api/v1/cache?limit=0")
        assert resp.status_code == 422
        resp = client.get("/api/v1/cache?limit=9999")
        assert resp.status_code == 422

    async def test_list_empty_cache(self, client: TestClient, auth_override, cache_store):
        resp = client.get("/api/v1/cache")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_skips_expired_entries(
        self, client: TestClient, auth_override, cache_store
    ):
        expired_ts = time.time() - 1000
        key = await _seed_entry(cache_store, "expired", ts=expired_ts)
        value = json.loads(cache_store.store[key])
        value["expires_at"] = expired_ts + 100  # expired 900s ago
        await cache_store.set(key, json.dumps(value), ex=100)

        resp = client.get("/api/v1/cache")
        assert resp.json() == []

    async def test_list_unauthorized(self, client: TestClient, unauthorized_override):
        resp = client.get("/api/v1/cache")
        assert resp.status_code == 401


class TestDeleteCacheEntry:
    async def test_delete_existing_entry(self, client: TestClient, auth_override, cache_store):
        key = await _seed_entry(cache_store, "delete me")

        resp = client.delete(f"/api/v1/cache/{key}")
        assert resp.status_code == 204
        assert key not in cache_store.store

        resp = client.get("/api/v1/cache")
        assert resp.json() == []

    async def test_delete_missing_entry_returns_404(
        self, client: TestClient, auth_override, cache_store
    ):
        missing = "a" * 64
        resp = client.delete(f"/api/v1/cache/{missing}")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Cache entry not found"

    async def test_delete_unauthorized(self, client: TestClient, unauthorized_override):
        resp = client.delete(f"/api/v1/cache/{'b' * 64}")
        assert resp.status_code == 401


class TestFlushCache:
    async def test_flush_removes_all_entries(
        self, client: TestClient, auth_override, cache_store
    ):
        await _seed_entry(cache_store, "prompt one")
        await _seed_entry(cache_store, "prompt two", verdict="block")
        decision_cache._hits = 5
        decision_cache._misses = 2

        resp = client.delete("/api/v1/cache")
        assert resp.status_code == 204
        assert cache_store.store == {}
        assert decision_cache._hits == 0
        assert decision_cache._misses == 0

    async def test_flush_unauthorized(self, client: TestClient, unauthorized_override):
        resp = client.delete("/api/v1/cache")
        assert resp.status_code == 401


class TestDecisionCacheHelpers:
    async def test_list_entries_returns_newest_first_dicts(
        self, client: TestClient, auth_override, cache_store
    ):
        older = time.time() - 10
        newer = time.time()
        key_older = await _seed_entry(cache_store, "aaa", ts=older)
        key_newer = await _seed_entry(cache_store, "bbb", ts=newer)

        cache = DecisionCache(ttl_seconds=300)
        entries = await cache.list_entries(limit=10)
        assert len(entries) == 2
        assert entries[0]["prompt_hash"] == key_newer
        assert entries[0]["verdict"] == "allow"
        assert entries[1]["prompt_hash"] == key_older

    async def test_count_and_delete_entry(
        self, client: TestClient, auth_override, cache_store
    ):
        cache = DecisionCache(ttl_seconds=300)
        await cache.set("prompt", "allow", "clean", [])
        key = DecisionCache._make_key("prompt")

        assert await cache.count() == 1
        assert await cache.delete_entry(key) is True
        assert await cache.delete_entry(key) is False
        assert await cache.count() == 0
