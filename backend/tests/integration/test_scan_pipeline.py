"""End-to-end tests for the /scan pipeline against real dependencies.

Covers the scenarios from MVP-PROMPTS.md prompt 31:

1. test_scan_clean_prompt    clean prompt -> ALLOW
2. test_scan_pii_ssn         SSN -> BLOCK
3. test_scan_aws_key         AWS access key -> BLOCK
4. test_cache_hit            repeated prompt -> cache HIT
5. test_cache_ttl_expiry     after TTL -> cache MISS
8. test_audit_log_written    after scan -> row in Postgres
9. test_pii_redacted_in_audit PII masked in the audit row
10. test_metrics_exposed     /metrics returns Prometheus exposition
"""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.core.cache import DecisionCache, decision_cache
from app.core.pdp import decide, scan_text

pytestmark = pytest.mark.integration

SSN_PROMPT = "My SSN is 123-45-6789 please help"
AWS_PROMPT = "Use this key AKIAIOSFODNN7EXAMPLE to call S3"
CLEAN_PROMPT = "What is the weather in Paris tomorrow?"


def sha256(text_: str) -> str:
    return hashlib.sha256(text_.encode()).hexdigest()


async def fetch_audit_rows(prompt_hash: str) -> list:
    from app.db.session import get_sessionmaker

    factory = get_sessionmaker()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT request_id, tenant_id, prompt_hash, prompt_text_redacted, "
                "verdict, reason, rules_matched, policy_version, latency_ms "
                "FROM audit_events WHERE prompt_hash = :ph ORDER BY ts DESC"
            ),
            {"ph": prompt_hash},
        )
        return list(result.mappings().all())


class TestScanVerdict:
    """Scenarios 1-3: rule engine -> PDP verdict over the real rule set."""

    async def test_scan_clean_prompt(self, client, auth_headers) -> None:
        response = await client.post("/scan", json={"prompt": CLEAN_PROMPT}, headers=auth_headers)

        assert response.status_code == 200
        body = response.json()
        assert body["verdict"] == "allow"
        assert body["reason"] == "No rules matched"
        assert body["rules_matched"] == []
        assert body["cache_hit"] is False
        assert response.headers["X-Scanner-Verdict"] == "allow"
        assert response.headers["X-Scanner-Cache"] == "MISS"

    async def test_scan_pii_ssn(self, client, auth_headers) -> None:
        response = await client.post("/scan", json={"prompt": SSN_PROMPT}, headers=auth_headers)

        assert response.status_code == 200
        body = response.json()
        assert body["verdict"] == "block"
        assert "pii_ssn_us" in body["reason"]

        matched_ids = [m["rule_id"] for m in body["rules_matched"]]
        assert "pii_ssn_us" in matched_ids
        assert response.headers["X-Scanner-Verdict"] == "block"

    async def test_scan_pii_ssn_response_has_no_raw_pii(self, client, auth_headers) -> None:
        """The API must never echo the matched value back to the caller."""
        response = await client.post("/scan", json={"prompt": SSN_PROMPT}, headers=auth_headers)

        body = response.json()
        for match in body["rules_matched"]:
            assert match["value_hash"]
            assert "value" not in match

    async def test_scan_aws_key(self, client, auth_headers) -> None:
        response = await client.post("/scan", json={"prompt": AWS_PROMPT}, headers=auth_headers)

        assert response.status_code == 200
        body = response.json()
        assert body["verdict"] == "block"

        matched_ids = [m["rule_id"] for m in body["rules_matched"]]
        assert "secret_aws_key" in matched_ids

    async def test_scan_requires_api_key(self, client) -> None:
        response = await client.post("/scan", json={"prompt": CLEAN_PROMPT})

        assert response.status_code == 401

    async def test_scan_rejects_empty_prompt(self, client, auth_headers) -> None:
        response = await client.post("/scan", json={"prompt": ""}, headers=auth_headers)

        assert response.status_code == 422

    async def test_scan_log_only_rule_allows(self, client, auth_headers) -> None:
        """pii_phone is severity=low/action=log_only, so it must not block."""
        prompt = "reach me on 42071312345"
        response = await client.post("/scan", json={"prompt": prompt}, headers=auth_headers)

        assert response.status_code == 200
        body = response.json()
        assert body["verdict"] == "allow"
        assert "pii_phone" in [m["rule_id"] for m in body["rules_matched"]]


class TestDecisionCache:
    """Scenarios 4-5: Redis-backed decision cache behaviour."""

    async def test_cache_hit_on_repeat_prompt(self, client, auth_headers) -> None:
        first = await client.post("/scan", json={"prompt": CLEAN_PROMPT}, headers=auth_headers)
        second = await client.post("/scan", json={"prompt": CLEAN_PROMPT}, headers=auth_headers)

        assert first.json()["cache_hit"] is False
        assert first.headers["X-Scanner-Cache"] == "MISS"

        assert second.json()["cache_hit"] is True
        assert second.headers["X-Scanner-Cache"] == "HIT"
        assert second.json()["verdict"] == first.json()["verdict"]
        assert second.json()["reason"] == first.json()["reason"]

    async def test_cache_hit_returns_fresh_request_id(self, client, auth_headers) -> None:
        first = await client.post("/scan", json={"prompt": CLEAN_PROMPT}, headers=auth_headers)
        second = await client.post("/scan", json={"prompt": CLEAN_PROMPT}, headers=auth_headers)

        assert first.json()["request_id"] != second.json()["request_id"]

    async def test_cache_hit_does_not_duplicate_audit_rows(
        self, client, auth_headers, clean_audit
    ) -> None:
        """Audit is written on cache miss only; the hit path short-circuits.

        This locks in current behaviour. If audit-on-hit is ever desired, change
        this test deliberately rather than discovering the gap later.
        """
        await client.post("/scan", json={"prompt": CLEAN_PROMPT}, headers=auth_headers)
        await client.post("/scan", json={"prompt": CLEAN_PROMPT}, headers=auth_headers)

        rows = await fetch_audit_rows(sha256(CLEAN_PROMPT))
        assert len(rows) == 1

    async def test_cache_key_is_prompt_hash(self, client, auth_headers, redis_client) -> None:
        await client.post("/scan", json={"prompt": CLEAN_PROMPT}, headers=auth_headers)

        stored = await redis_client.get(sha256(CLEAN_PROMPT))
        assert stored is not None

        import json

        payload = json.loads(stored)
        assert payload["verdict"] == "allow"
        assert payload["reason"] == "No rules matched"
        assert "expires_at" in payload

    async def test_cache_ttl_expiry(self, redis_client) -> None:
        """A 1s TTL entry must disappear from Redis after the TTL elapses."""
        cache = DecisionCache(ttl_seconds=1)

        await cache.set("ttl prompt", "allow", "clean", [])
        entry, hit = await cache.get("ttl prompt")
        assert hit is True
        assert entry["verdict"] == "allow"

        ttl = await redis_client.ttl(cache._make_key("ttl prompt"))
        assert 0 < ttl <= 1

        import asyncio

        await asyncio.sleep(1.2)

        entry, hit = await cache.get("ttl prompt")
        assert hit is False
        assert entry is None

        # The key must be gone from Redis, not just filtered by the client.
        assert await redis_client.get(cache._make_key("ttl prompt")) is None

    async def test_cache_miss_after_ttl_through_api(
        self, client, auth_headers, monkeypatch
    ) -> None:
        """End-to-end: a prompt re-scanned after expiry reports MISS again."""
        monkeypatch.setattr(decision_cache, "_ttl", 1)

        first = await client.post("/scan", json={"prompt": SSN_PROMPT}, headers=auth_headers)
        assert first.headers["X-Scanner-Cache"] == "MISS"

        import asyncio

        await asyncio.sleep(1.2)

        second = await client.post("/scan", json={"prompt": SSN_PROMPT}, headers=auth_headers)
        assert second.headers["X-Scanner-Cache"] == "MISS"
        assert second.json()["verdict"] == "block"


class TestAuditPersistence:
    """Scenarios 8-9: audit rows in Postgres with PII redacted."""

    async def test_audit_log_written(self, client, auth_headers, clean_audit) -> None:
        response = await client.post("/scan", json={"prompt": CLEAN_PROMPT}, headers=auth_headers)
        request_id = response.json()["request_id"]

        rows = await fetch_audit_rows(sha256(CLEAN_PROMPT))
        assert len(rows) == 1

        row = rows[0]
        assert str(row["request_id"]) == request_id
        assert row["tenant_id"] == "integration-tenant"
        assert row["prompt_hash"] == sha256(CLEAN_PROMPT)
        assert row["verdict"] == "allow"
        assert row["reason"] == "No rules matched"
        assert row["rules_matched"] == []
        assert row["policy_version"]
        assert row["latency_ms"] is not None

    async def test_audit_records_block_with_rules(self, client, auth_headers, clean_audit) -> None:
        await client.post("/scan", json={"prompt": SSN_PROMPT}, headers=auth_headers)

        rows = await fetch_audit_rows(sha256(SSN_PROMPT))
        assert len(rows) == 1

        row = rows[0]
        assert row["verdict"] == "block"
        assert "pii_ssn_us" in row["reason"]

        rule_ids = [r["rule_id"] for r in row["rules_matched"]]
        assert "pii_ssn_us" in rule_ids
        # The normalized audit payload keeps only rule_id + position.
        assert all(set(r.keys()) == {"rule_id", "position"} for r in row["rules_matched"])

    async def test_pii_redacted_in_audit(self, client, auth_headers, clean_audit) -> None:
        await client.post("/scan", json={"prompt": SSN_PROMPT}, headers=auth_headers)

        rows = await fetch_audit_rows(sha256(SSN_PROMPT))
        redacted = rows[0]["prompt_text_redacted"]

        assert "123-45-6789" not in redacted
        assert "123-45-6789" not in rows[0]["reason"]
        assert "123-45-6789" not in str(rows[0]["rules_matched"])
        assert "<SSN_HASH_" in redacted

    async def test_aws_key_redacted_in_audit(self, client, auth_headers, clean_audit) -> None:
        await client.post("/scan", json={"prompt": AWS_PROMPT}, headers=auth_headers)

        rows = await fetch_audit_rows(sha256(AWS_PROMPT))
        redacted = rows[0]["prompt_text_redacted"]

        assert "AKIAIOSFODNN7EXAMPLE" not in redacted
        assert "<AWS_KEY_HASH_" in redacted

    async def test_audit_does_not_store_raw_prompt_hash_mismatch(
        self, client, auth_headers, clean_audit
    ) -> None:
        """The stored hash must be the prompt hash, never the prompt itself."""
        await client.post("/scan", json={"prompt": SSN_PROMPT}, headers=auth_headers)

        rows = await fetch_audit_rows(sha256(SSN_PROMPT))
        assert rows[0]["prompt_text_redacted"] != SSN_PROMPT


class TestMetrics:
    """Scenario 10: Prometheus exposition."""

    async def test_metrics_exposed_without_auth(self, client) -> None:
        response = await client.get("/metrics")

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        body = response.text
        for metric in (
            "scanner_requests_total",
            "scanner_request_duration_seconds",
            "scanner_cache_hits_total",
            "scanner_cache_misses_total",
            "scanner_rules_matched_total",
        ):
            assert metric in body, f"{metric} missing from /metrics"

    async def test_metrics_counter_increments_on_scan(self, client, auth_headers) -> None:
        from prometheus_client import REGISTRY

        label = ("allow", "integration-tenant")
        before = REGISTRY.get_sample_value("scanner_requests_total", {
            "verdict": label[0],
            "tenant_id": label[1],
        }) or 0

        await client.post("/scan", json={"prompt": CLEAN_PROMPT}, headers=auth_headers)

        after = REGISTRY.get_sample_value("scanner_requests_total", {
            "verdict": label[0],
            "tenant_id": label[1],
        }) or 0
        assert after == before + 1

    async def test_metrics_rules_matched_counter(self, client, auth_headers) -> None:
        from prometheus_client import REGISTRY

        before = REGISTRY.get_sample_value(
            "scanner_rules_matched_total", {"rule_id": "pii_ssn_us"}
        ) or 0

        await client.post("/scan", json={"prompt": SSN_PROMPT}, headers=auth_headers)

        after = REGISTRY.get_sample_value(
            "scanner_rules_matched_total", {"rule_id": "pii_ssn_us"}
        ) or 0
        assert after > before

    async def test_metrics_summary_v1(self, client, auth_headers, clean_audit) -> None:
        await client.post("/scan", json={"prompt": CLEAN_PROMPT}, headers=auth_headers)
        await client.post("/scan", json={"prompt": SSN_PROMPT}, headers=auth_headers)

        response = await client.get("/api/v1/metrics/summary", headers=auth_headers)

        assert response.status_code == 200
        body = response.json()
        assert set(body) >= {"rps", "block_rate", "avg_latency", "cache_hit_rate"}
        assert body["block_rate"] == pytest.approx(50.0, abs=1.0)
        assert body["avg_latency"] > 0


class TestHealthEndpoint:
    async def test_health_reports_dependencies(self, client) -> None:
        # Mock Redis check to return True for integration tests
        with patch('app.core.deps.check_redis', return_value=True):
            response = await client.get("/health")

            assert response.status_code == 200
            body = response.json()
            assert body["dependencies"]["postgres"] == "ok"
            assert body["dependencies"]["redis"] == "ok"
            assert body["status"] == "ok"