from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class TestHealthEndpoint:
    @patch("app.core.deps.check_all_dependencies")
    def test_health_returns_200_when_all_ok(self, mock_check: AsyncMock, client: TestClient) -> None:
        mock_check.return_value = {"postgres": True, "redis": True}
        
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["dependencies"]["postgres"] == "ok"
        assert data["dependencies"]["redis"] == "ok"
        assert "X-Scanner-Status" in resp.headers
        assert resp.headers["X-Scanner-Status"] == "ok"

    @patch("app.core.deps.check_all_dependencies")
    def test_health_returns_200_when_degraded_postgres_fails(self, mock_check: AsyncMock, client: TestClient) -> None:
        mock_check.return_value = {"postgres": False, "redis": True}
        
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["dependencies"]["postgres"] == "fail"
        assert data["dependencies"]["redis"] == "ok"
        assert resp.headers["X-Scanner-Status"] == "degraded"

    @patch("app.core.deps.check_all_dependencies")
    def test_health_returns_200_when_degraded_redis_fails(self, mock_check: AsyncMock, client: TestClient) -> None:
        mock_check.return_value = {"postgres": True, "redis": False}
        
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["dependencies"]["postgres"] == "ok"
        assert data["dependencies"]["redis"] == "fail"
        assert resp.headers["X-Scanner-Status"] == "degraded"

    @patch("app.core.deps.check_all_dependencies")
    def test_health_returns_503_when_all_down(self, mock_check: AsyncMock, client: TestClient) -> None:
        mock_check.return_value = {"postgres": False, "redis": False}
        
        resp = client.get("/health")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "down"
        assert data["dependencies"]["postgres"] == "fail"
        assert data["dependencies"]["redis"] == "fail"
        assert resp.headers["X-Scanner-Status"] == "down"

    @patch("app.core.deps.check_all_dependencies")
    def test_health_has_version(self, mock_check: AsyncMock, client: TestClient) -> None:
        mock_check.return_value = {"postgres": True, "redis": True}
        
        resp = client.get("/health")
        data = resp.json()
        assert "version" in data

    @patch("app.core.deps.check_all_dependencies")
    def test_health_has_uptime(self, mock_check: AsyncMock, client: TestClient) -> None:
        mock_check.return_value = {"postgres": True, "redis": True}
        
        resp = client.get("/health")
        data = resp.json()
        assert "uptime_seconds" in data
        assert data["uptime_seconds"] >= 0

    @patch("app.core.deps.check_all_dependencies")
    def test_health_has_dependencies(self, mock_check: AsyncMock, client: TestClient) -> None:
        mock_check.return_value = {"postgres": True, "redis": True}
        
        resp = client.get("/health")
        data = resp.json()
        assert "dependencies" in data
        assert "postgres" in data["dependencies"]
        assert "redis" in data["dependencies"]

    @patch("app.core.deps.check_all_dependencies")
    def test_health_no_auth_required(self, mock_check: AsyncMock, client: TestClient) -> None:
        mock_check.return_value = {"postgres": True, "redis": True}
        
        resp = client.get("/health")
        assert resp.status_code == 200
