from __future__ import annotations

from fastapi.testclient import TestClient


class TestHealthEndpoint:
    def test_health_returns_200(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_status_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        data = resp.json()
        assert data["status"] == "ok"

    def test_health_has_version(self, client: TestClient) -> None:
        resp = client.get("/health")
        data = resp.json()
        assert "version" in data

    def test_health_has_uptime(self, client: TestClient) -> None:
        resp = client.get("/health")
        data = resp.json()
        assert "uptime_seconds" in data
        assert data["uptime_seconds"] >= 0

    def test_health_has_dependencies(self, client: TestClient) -> None:
        resp = client.get("/health")
        data = resp.json()
        assert "dependencies" in data
        assert "postgres" in data["dependencies"]
        assert "redis" in data["dependencies"]

    def test_health_no_auth_required(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
