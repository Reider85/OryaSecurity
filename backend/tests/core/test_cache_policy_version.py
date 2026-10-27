"""Decision cache is scoped by policy_version (EP-12/Prompt 28):

switching the OPA bundle must invalidate cached verdicts automatically,
while entries keep the version that produced them (for the audit log).
"""

from __future__ import annotations

import hashlib

import fakeredis
import pytest

from app.core.cache import DecisionCache, _CACHE_KEY_RE


@pytest.fixture(autouse=True)
async def use_fake_redis(monkeypatch: pytest.MonkeyPatch):
    # Local async fixture: the shared `fake_redis` conftest fixture returns a
    # *sync* client, which cannot be awaited (pre-existing issue).
    client = fakeredis.FakeAsyncRedis(decode_responses=True)

    async def _get_redis():
        return client

    monkeypatch.setattr("app.core.cache.get_redis_connection", _get_redis)
    yield client
    await client.aclose()


class TestPolicyVersionScoping:
    @pytest.mark.asyncio
    async def test_version_scoped_key(self):
        cache = DecisionCache(ttl_seconds=60)
        await cache.set("hello", "allow", "clean", [], policy_version="1.0.0")

        hit_entry, hit = await cache.get("hello", policy_version="1.0.0")
        assert hit is True
        assert hit_entry["policy_version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_bundle_switch_invalidates_cache(self):
        cache = DecisionCache(ttl_seconds=60)
        await cache.set("hello", "block", "old policy", [], policy_version="1.0.0")

        entry, hit = await cache.get("hello", policy_version="2.0.0")
        assert hit is False
        assert entry is None

        # old entry still reachable under the old version (audit accuracy)
        old_entry, old_hit = await cache.get("hello", policy_version="1.0.0")
        assert old_hit is True
        assert old_entry["verdict"] == "block"

    @pytest.mark.asyncio
    async def test_key_matches_sha256_of_versioned_prompt(self):
        expected = hashlib.sha256(b"1.0.0:hello").hexdigest()
        assert DecisionCache._make_key("hello", "1.0.0") == expected
        assert _CACHE_KEY_RE.match(expected)  # still scan-able as a 64-hex key

    @pytest.mark.asyncio
    async def test_legacy_key_without_version_unchanged(self):
        expected = hashlib.sha256(b"hello").hexdigest()
        assert DecisionCache._make_key("hello") == expected
        assert DecisionCache._make_key("hello", None) == expected

    @pytest.mark.asyncio
    async def test_set_without_version_still_works(self):
        cache = DecisionCache(ttl_seconds=60)
        await cache.set("plain", "allow", "clean", [])
        entry, hit = await cache.get("plain")
        assert hit is True
        assert entry["policy_version"] is None
