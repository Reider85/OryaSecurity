from __future__ import annotations

import asyncio
import time
from typing import Any

import numpy as np
import pytest
import pytest_asyncio
import redis.asyncio as redis
import fakeredis

from app.core.cache_embedding import EmbeddingCache, EmbeddingCacheStats


def _make_test_embedding() -> np.ndarray:
    """Helper to create test embedding."""
    return np.random.randn(1024).astype(np.float32)


@pytest.fixture
async def fake_redis_bytes() -> fakeredis.FakeRedis:
    """Fake Redis client for binary data (no decode_responses)."""
    client = fakeredis.FakeRedis(decode_responses=False)
    yield client
    await client.aclose()


@pytest.fixture(autouse=True)
def use_fake_redis_bytes(fake_redis_bytes: fakeredis.FakeRedis, monkeypatch: pytest.MonkeyPatch):
    """Route EmbeddingCache through fake_redis_bytes for unit tests."""
    async def _get_redis_bytes():
        return fake_redis_bytes
    
    monkeypatch.setattr("app.core.cache_embedding.get_redis_bytes", _get_redis_bytes)
    return fake_redis_bytes


@pytest.fixture
def cache() -> EmbeddingCache:
    """EmbeddingCache instance for testing."""
    return EmbeddingCache(ttl_seconds=1, max_entries=3)


@pytest.mark.asyncio
async def test_get_miss(cache: EmbeddingCache) -> None:
    """Test get() when key doesn't exist."""
    result = await cache.get("test text")
    assert result is None


@pytest.mark.asyncio
async def test_set_and_get(cache: EmbeddingCache) -> None:
    """Test roundtrip set() and get()."""
    text = "test text"
    embedding = _make_test_embedding()
    
    # Set
    await cache.set(text, embedding)
    
    # Get
    result = await cache.get(text)
    assert result is not None
    assert result.shape == (1024,)
    assert np.allclose(result, embedding, rtol=1e-5)


@pytest.mark.asyncio
async def test_get_shape_mismatch(cache: EmbeddingCache) -> None:
    """Test get() with wrong shape data."""
    text = "wrong shape"
    wrong_embedding = np.random.randn(512).astype(np.float32)  # Wrong size
    
    # Store wrong shape
    await cache.set(text, wrong_embedding)
    
    # Should return None due to shape mismatch
    result = await cache.get(text)
    assert result is None


@pytest.mark.asyncio
async def test_ttl_expiry(cache: EmbeddingCache) -> None:
    """Test TTL expiration."""
    text = "ttl test"
    embedding = _make_test_embedding()
    
    # Set with short TTL
    cache._ttl = 1
    await cache.set(text, embedding)
    
    # Should be available immediately
    result = await cache.get(text)
    assert result is not None
    
    # Wait for expiration
    await asyncio.sleep(1.1)
    
    # Should be expired
    result = await cache.get(text)
    assert result is None


@pytest.mark.asyncio
async def test_stats(cache: EmbeddingCache) -> None:
    """Test stats() method."""
    text = "stats test"
    embedding = np.random.randn(1024).astype(np.float32)
    
    # Initially empty
    stats = await cache.stats()
    assert stats.hits == 0
    assert stats.misses == 0
    assert stats.total_entries == 0
    
    # Get miss
    await cache.get(text)
    stats = await cache.stats()
    assert stats.hits == 0
    assert stats.misses == 1
    
    # Set and hit
    await cache.set(text, embedding)
    await cache.get(text)
    stats = await cache.stats()
    assert stats.hits == 1
    assert stats.misses == 1
    assert stats.total_entries == 1


@pytest.mark.asyncio
async def test_lru_eviction(cache: EmbeddingCache) -> None:
    """Test LRU eviction when max_entries exceeded."""
    # Set max_entries to 3
    cache._max_entries = 3
    
    embeddings = [_make_test_embedding() for _ in range(4)]
    texts = [f"text {i}" for i in range(4)]
    
    # Store 4 entries (should evict oldest)
    for text, embedding in zip(texts, embeddings):
        await cache.set(text, embedding)
    
    # First entry should be evicted
    result = await cache.get("text 0")
    assert result is None
    
    # Last 3 entries should still be there
    for i in range(1, 4):
        result = await cache.get(f"text {i}")
        assert result is not None


@pytest.mark.asyncio
async def test_graceful_fallback_on_redis_error(cache: EmbeddingCache, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test graceful fallback when Redis operations fail."""
    text = "error test"
    embedding = _make_test_embedding()
    
    # Mock Redis to raise exception
    async def mock_redis_error():
        raise redis.RedisError("Connection failed")
    
    monkeypatch.setattr("app.core.cache_embedding.get_redis_bytes", mock_redis_error)
    
    # Should not raise exception
    result = await cache.get(text)
    assert result is None
    
    # Should not raise exception
    await cache.set(text, embedding)
    
    # Stats should still work
    stats = await cache.stats()
    assert stats.hits == 0
    assert stats.misses == 1


@pytest.mark.asyncio
async def test_key_generation_consistency(cache: EmbeddingCache) -> None:
    """Test that same text generates same key."""
    text = "consistent text"
    
    key1 = cache._make_key(text)
    key2 = cache._make_key(text)
    
    assert key1 == key2
    assert key1.startswith("emb:")


@pytest.mark.asyncio
async def test_multiple_operations(cache: EmbeddingCache) -> None:
    """Test multiple cache operations."""
    texts = [f"text {i}" for i in range(10)]
    embeddings = [_make_test_embedding() for _ in range(10)]
    
    # Set all
    for text, embedding in zip(texts, embeddings):
        await cache.set(text, embedding)
    
    # Get all
    for text, expected_embedding in zip(texts, embeddings):
        result = await cache.get(text)
        assert result is not None
        assert np.allclose(result, expected_embedding, rtol=1e-5)
    
    # Stats
    stats = await cache.stats()
    assert stats.hits == 0  # No gets yet
    assert stats.misses == 0
    assert stats.total_entries == 10
    
    # Mixed gets and sets
    await cache.get("text 0")  # Hit
    await cache.get("nonexistent")  # Miss
    
    stats = await cache.stats()
    assert stats.hits == 1
    assert stats.misses == 1


@pytest.mark.asyncio
async def test_embedding_bytes_length(cache: EmbeddingCache, fake_redis_bytes: fakeredis.FakeRedis) -> None:
    """Test that embeddings are stored as correct byte length."""
    text = "bytes test"
    embedding = np.random.randn(1024).astype(np.float32)
    
    # Store
    await cache.set(text, embedding)
    
    # Verify bytes length directly in Redis
    key = cache._make_key(text)
    stored_bytes = await fake_redis_bytes.get(key)
    assert stored_bytes is not None
    assert len(stored_bytes) == 1024 * 4  # 1024 dimensions * 4 bytes (FP32)
    assert stored_bytes == embedding.tobytes()


