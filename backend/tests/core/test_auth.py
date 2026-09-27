from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestAuth:
    def test_missing_auth_returns_401(self, client: TestClient) -> None:
        resp = client.post("/scan", json={"prompt": "hello"})
        assert resp.status_code == 401

    def test_invalid_key_returns_401(self, client: TestClient) -> None:
        resp = client.post(
            "/scan",
            json={"prompt": "hello"},
            headers={"Authorization": "Bearer invalid-key-12345"},
        )
        assert resp.status_code == 401

    def test_valid_key_allows(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.post("/scan", json={"prompt": "hello"}, headers=auth_headers)
        assert resp.status_code == 200

    def test_health_no_auth_needed(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_metrics_no_auth_needed(self, client: TestClient) -> None:
        resp = client.get("/metrics")
        assert resp.status_code == 200
