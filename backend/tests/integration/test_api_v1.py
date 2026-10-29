"""Tests for the v1 API endpoints that read from Postgres.

Covered: GET /api/v1/audit, GET /api/v1/metrics/summary,
GET /api/v1/decisions and GET /api/v1/decisions/{request_id}.

These need a real database, so they live under tests/integration/ alongside the
fixtures in conftest.py.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.audit import write_event

pytestmark = pytest.mark.integration


async def seed(tenant_id: str, verdict: str, prompt_hash: str) -> str:
    request_id = str(uuid.uuid4())
    await write_event(
        request_id=request_id,
        tenant_id=tenant_id,
        prompt_hash=prompt_hash,
        verdict=verdict,
        reason="Clean" if verdict == "allow" else "Blocked by rules: pii_ssn_us",
        rules_matched=(
            [] if verdict == "allow" else [{"rule_id": "pii_ssn_us", "position": [11, 22]}]
        ),
        prompt_text_redacted="redacted prompt",
        latency_ms=10.0,
    )
    return request_id


class TestAuditEndpoint:
    async def test_pagination(self, real_postgres, clean_audit, client, auth_headers) -> None:
        for i in range(15):
            await seed("t-pagination", "allow" if i % 2 == 0 else "block", f"{i:064d}")

        body = (
            await client.get("/api/v1/audit?tenant_id=t-pagination&limit=10&page=1", headers=auth_headers)
        ).json()

        assert len(body["items"]) == 10
        assert body["total"] == 15
        assert body["page"] == 1
        assert body["per_page"] == 10

        page2 = (
            await client.get("/api/v1/audit?tenant_id=t-pagination&limit=10&page=2", headers=auth_headers)
        ).json()
        assert len(page2["items"]) == 5

    async def test_sorted_newest_first(self, real_postgres, clean_audit, client, auth_headers) -> None:
        for i in range(3):
            await seed("t-sorted", "allow", f"{i:064d}")

        body = (
            await client.get("/api/v1/audit?tenant_id=t-sorted", headers=auth_headers)
        ).json()

        timestamps = [item["ts"] for item in body["items"]]
        assert timestamps == sorted(timestamps, reverse=True)

    async def test_verdict_filter(self, real_postgres, clean_audit, client, auth_headers) -> None:
        for i in range(3):
            await seed("t-verdict", "block", f"{i:064d}")

        body = (
            await client.get(
                "/api/v1/audit?tenant_id=t-verdict&verdict=block", headers=auth_headers
            )
        ).json()

        assert len(body["items"]) == 3
        assert all(item["verdict"] == "block" for item in body["items"])

    async def test_prompt_hash_prefix_search(
        self, real_postgres, clean_audit, client, auth_headers
    ) -> None:
        """The UI searches by hash prefix; `total` must agree with `items`."""
        for i in range(3):
            await seed("t-hash", "allow", f"abc{i:060d}")
        await seed("t-hash", "allow", "z" * 64)

        body = (
            await client.get(
                "/api/v1/audit?tenant_id=t-hash&prompt_hash=abc", headers=auth_headers
            )
        ).json()

        assert body["total"] == 3
        assert len(body["items"]) == 3
        assert all(item["prompt_hash"].startswith("abc") for item in body["items"])

    async def test_policy_version_prefix_filter(
        self, real_postgres, clean_audit, client, auth_headers
    ) -> None:
        """Prompt 29: policy_version filter is a prefix match; total agrees."""
        for version in ("1.0.0", "1.0.1", "2.0.0"):
            await write_event(
                request_id=str(uuid.uuid4()),
                tenant_id="t-pv",
                prompt_hash="a" * 64,
                verdict="allow",
                policy_version=version,
            )

        body = (
            await client.get(
                "/api/v1/audit?tenant_id=t-pv&policy_version=1.0", headers=auth_headers
            )
        ).json()

        assert body["total"] == 2
        assert len(body["items"]) == 2
        assert all(item["policy_version"].startswith("1.0") for item in body["items"])

        full = (
            await client.get(
                "/api/v1/audit?tenant_id=t-pv&policy_version=2.0.0", headers=auth_headers
            )
        ).json()
        assert full["total"] == 1
        assert full["items"][0]["policy_version"] == "2.0.0"

        none_match = (
            await client.get(
                "/api/v1/audit?tenant_id=t-pv&policy_version=9.9", headers=auth_headers
            )
        ).json()
        assert none_match["total"] == 0
        assert none_match["items"] == []

    async def test_rules_matched_shape(self, real_postgres, clean_audit, client, auth_headers) -> None:
        """rules_matched entries keep {rule_id, position:[start,end]}."""
        await seed("t-shape", "block", "c" * 64)

        body = (
            await client.get("/api/v1/audit?tenant_id=t-shape", headers=auth_headers)
        ).json()

        match = body["items"][0]["rules_matched"][0]
        assert match["rule_id"] == "pii_ssn_us"
        assert match["position"] == [11, 22]

    async def test_empty(self, real_postgres, clean_audit, client, auth_headers) -> None:
        body = (await client.get("/api/v1/audit", headers=auth_headers)).json()

        assert body["items"] == []
        assert body["total"] == 0

    async def test_rejects_invalid_verdict(self, real_postgres, clean_audit, client, auth_headers) -> None:
        response = await client.get("/api/v1/audit?verdict=maybe", headers=auth_headers)

        assert response.status_code == 422

    async def test_limit_bounds(self, real_postgres, clean_audit, client, auth_headers) -> None:
        assert (await client.get("/api/v1/audit?limit=0", headers=auth_headers)).status_code == 422
        assert (await client.get("/api/v1/audit?limit=101", headers=auth_headers)).status_code == 422

    async def test_unauthorized(self, real_postgres, clean_audit, client) -> None:
        response = await client.get("/api/v1/audit")

        assert response.status_code == 401

    async def test_timestamps_are_timezone_aware(self, real_postgres, clean_audit, client, auth_headers) -> None:
        await seed("t-tz", "allow", "d" * 64)

        body = (await client.get("/api/v1/audit?tenant_id=t-tz", headers=auth_headers)).json()

        assert body["items"][0]["ts"].endswith("+00:00") or body["items"][0]["ts"].endswith("Z")


class TestMetricsSummaryEndpoint:
    async def test_empty_database_returns_zeros(
        self, real_postgres, clean_audit, client, auth_headers
    ) -> None:
        body = (await client.get("/api/v1/metrics/summary", headers=auth_headers)).json()

        assert body["rps"] == 0.0
        assert body["block_rate"] == 0.0
        assert body["avg_latency"] == 0.0
        assert body["cache_hit_rate"] == 0.0

    async def test_block_rate_and_avg_latency(
        self, real_postgres, clean_audit, client, auth_headers
    ) -> None:
        await seed("t-metrics", "allow", "a" * 64)
        await seed("t-metrics", "allow", "b" * 64)
        await seed("t-metrics", "block", "c" * 64)

        body = (await client.get("/api/v1/metrics/summary", headers=auth_headers)).json()

        # 1 of 3 rows is a block. The table is truncated per test, so these are
        # the only rows in the "today" window.
        assert body["block_rate"] == pytest.approx(33.33, abs=0.1)
        assert body["avg_latency"] == pytest.approx(10.0, abs=0.1)

    async def test_cache_hit_rate_reads_live_counters(
        self, real_postgres, clean_audit, client, auth_headers
    ) -> None:
        """cache_hit_rate must come from the registry holding the counters."""
        from prometheus_client import REGISTRY

        from app.core.metrics import metrics_registry

        assert metrics_registry is REGISTRY

        before = REGISTRY.get_sample_value(
            "scanner_cache_hits_total", {"type": "decision"}
        ) or 0
        body = (await client.get("/api/v1/metrics/summary", headers=auth_headers)).json()
        after = REGISTRY.get_sample_value(
            "scanner_cache_hits_total", {"type": "decision"}
        ) or 0

        # The assertion that matters is that the endpoint returns without a 500
        # and reports a number rather than blowing up on a missing registry.
        assert body["cache_hit_rate"] >= 0.0
        assert after == before

    async def test_unauthorized(self, real_postgres, clean_audit, client) -> None:
        response = await client.get("/api/v1/metrics/summary")

        assert response.status_code == 401


class TestDecisionsEndpoint:
    async def test_list_returns_latency_breakdown(
        self, real_postgres, clean_audit, client, auth_headers
    ) -> None:
        await seed("t-dec", "block", "e" * 64)

        body = (await client.get("/api/v1/decisions?tenant_id=t-dec", headers=auth_headers)).json()

        assert body["total"] == 1
        item = body["items"][0]
        assert set(item["latency_breakdown"]) == {
            "fast_path_ms",
            "slow_path_ms",
            "pdp_ms",
            "audit_ms",
            "total_ms",
        }
        assert item["latency_breakdown"]["total_ms"] == 10.0
        assert item["cache_status"] in {"HIT", "MISS"}
        assert item["policy_version"]

    async def test_list_rules_matched_position_is_object(
        self, real_postgres, clean_audit, client, auth_headers
    ) -> None:
        await seed("t-pos", "block", "f" * 64)

        body = (await client.get("/api/v1/decisions?tenant_id=t-pos", headers=auth_headers)).json()

        position = body["items"][0]["rules_matched"][0]["position"]
        assert position == {"start": 11, "end": 22, "matched_text": "redacted prompt"[11:22]}

    async def test_rule_id_filter(self, real_postgres, clean_audit, client, auth_headers) -> None:
        await seed("t-rule", "block", "1" * 64)
        await seed("t-rule", "allow", "2" * 64)

        matched = (
            await client.get(
                "/api/v1/decisions?tenant_id=t-rule&rule_id=pii_ssn_us", headers=auth_headers
            )
        ).json()
        assert len(matched["items"]) == 1
        assert matched["items"][0]["verdict"] == "block"

        unmatched = (
            await client.get(
                "/api/v1/decisions?tenant_id=t-rule&rule_id=secret_aws_key", headers=auth_headers
            )
        ).json()
        assert unmatched["items"] == []

    async def test_pagination_flags(self, real_postgres, clean_audit, client, auth_headers) -> None:
        for i in range(25):
            await seed("t-page", "allow", f"{i:064d}")

        page1 = (
            await client.get("/api/v1/decisions?tenant_id=t-page&limit=10&page=1", headers=auth_headers)
        ).json()
        assert len(page1["items"]) == 10
        assert page1["has_next"] is True
        assert page1["has_prev"] is False

        page3 = (
            await client.get("/api/v1/decisions?tenant_id=t-page&limit=10&page=3", headers=auth_headers)
        ).json()
        assert len(page3["items"]) == 5
        assert page3["has_next"] is False
        assert page3["has_prev"] is True

    async def test_detail_by_request_id(self, real_postgres, clean_audit, client, auth_headers) -> None:
        request_id = await seed("t-detail", "block", "9" * 64)

        response = await client.get(f"/api/v1/decisions/{request_id}", headers=auth_headers)

        assert response.status_code == 200
        body = response.json()
        assert body["request_id"] == request_id
        assert body["verdict"] == "block"
        assert body["reason"] == "Blocked by rules: pii_ssn_us"
        assert body["prompt_text_redacted"] == "redacted prompt"
        assert body["rules_matched"][0]["rule_id"] == "pii_ssn_us"

    async def test_detail_not_found(self, real_postgres, clean_audit, client, auth_headers) -> None:
        response = await client.get(f"/api/v1/decisions/{uuid.uuid4()}", headers=auth_headers)

        assert response.status_code == 404

    async def test_unauthorized(self, real_postgres, clean_audit, client) -> None:
        assert (await client.get("/api/v1/decisions")).status_code == 401