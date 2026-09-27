from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestScanEndpoint:
    def test_scan_clean_prompt_allows(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.post("/scan", json={"prompt": "Hello, how are you?"}, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["verdict"] == "allow"
        assert "request_id" in data
        assert "latency_ms" in data
        assert data["cache_hit"] is False

    def test_scan_pii_ssn_blocks(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.post("/scan", json={"prompt": "My SSN is 123-45-6789"}, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["verdict"] == "block"
        assert any(r["rule_id"] == "pii_ssn_us" for r in data["rules_matched"])

    def test_scan_pii_email_blocks(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.post("/scan", json={"prompt": "Contact me at john@example.com"}, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["verdict"] == "block"
        assert any(r["rule_id"] == "pii_email" for r in data["rules_matched"])

    def test_scan_aws_key_blocks(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.post("/scan", json={"prompt": "Key: AKIAIOSFODNN7EXAMPLE"}, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["verdict"] == "block"
        assert any(r["rule_id"] == "secret_aws_key" for r in data["rules_matched"])

    def test_scan_jwt_blocks(self, client: TestClient, auth_headers: dict) -> None:
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        resp = client.post("/scan", json={"prompt": f"Token: {jwt}"}, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["verdict"] == "block"
        assert any(r["rule_id"] == "secret_jwt" for r in data["rules_matched"])

    def test_scan_credit_card_luhn_valid(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.post("/scan", json={"prompt": "Card: 4111111111111111"}, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["verdict"] == "block"
        assert any(r["rule_id"] == "secret_credit_card" for r in data["rules_matched"])

    def test_scan_credit_card_luhn_invalid(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.post("/scan", json={"prompt": "Card: 4111111111111112"}, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["verdict"] == "allow"

    def test_scan_returns_x_scanner_verdict_header(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.post("/scan", json={"prompt": "Hello"}, headers=auth_headers)
        assert "X-Scanner-Verdict" in resp.headers
        assert resp.headers["X-Scanner-Verdict"] == "allow"

    def test_scan_returns_request_id_header(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.post("/scan", json={"prompt": "Hello"}, headers=auth_headers)
        assert "X-Scanner-Request-Id" in resp.headers

    def test_scan_cache_hit(self, client: TestClient, auth_headers: dict) -> None:
        prompt = "Unique test prompt for cache"
        resp1 = client.post("/scan", json={"prompt": prompt}, headers=auth_headers)
        assert resp1.json()["cache_hit"] is False

        resp2 = client.post("/scan", json={"prompt": prompt}, headers=auth_headers)
        assert resp2.json()["cache_hit"] is True
        assert resp2.headers.get("X-Scanner-Cache") == "HIT"

    def test_scan_with_tenant_id(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.post(
            "/scan",
            json={"prompt": "Hello", "tenant_id": "team-alpha"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_scan_with_metadata(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.post(
            "/scan",
            json={"prompt": "Hello", "metadata": {"source": "test"}},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_scan_empty_prompt_rejected(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.post("/scan", json={"prompt": ""}, headers=auth_headers)
        assert resp.status_code == 422

    def test_scan_no_auth_returns_401(self, client: TestClient) -> None:
        resp = client.post("/scan", json={"prompt": "Hello"})
        assert resp.status_code == 401

    def test_scan_invalid_key_returns_401(self, client: TestClient) -> None:
        resp = client.post(
            "/scan",
            json={"prompt": "Hello"},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401

    def test_scan_multiple_pii_matches(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.post(
            "/scan",
            json={"prompt": "SSN: 123-45-6789 and email: test@test.com"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["verdict"] == "block"
        assert len(data["rules_matched"]) >= 2
