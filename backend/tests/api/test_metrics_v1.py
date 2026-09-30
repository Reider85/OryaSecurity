import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.db.session import get_session
from app.core.auth import verify_api_key
from app.models.audit import AuditEvent, RuleMatch
from tests.conftest import test_auth_headers


@pytest.mark.asyncio
async def test_get_metrics_summary_empty(client: TestClient):
    """Test metrics summary endpoint with no data"""
    response = client.get("/api/v1/metrics/summary", headers=test_auth_headers)
    assert response.status_code == 200
    
    data = response.json()
    assert "rps" in data
    assert "block_rate" in data
    assert "avg_latency" in data
    assert "cache_hit_rate" in data
    assert "timestamp" in data
    
    # Should be zero when no data
    assert data["rps"] == 0.0
    assert data["block_rate"] == 0.0
    assert data["avg_latency"] == 0.0
    assert data["cache_hit_rate"] == 0.0


@pytest.mark.asyncio
async def test_get_metrics_summary_with_data(client: TestClient, session: AsyncSession):
    """Test metrics summary endpoint with audit data"""
    # Create test audit events
    now = datetime.utcnow()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Insert test data
    await session.execute(
        """
        INSERT INTO audit_events (ts, request_id, prompt_hash, verdict, reason, rules_matched, latency_ms)
        VALUES 
            (:ts1, :req1, :hash1, 'allow', 'Clean prompt', '[]', 10.5),
            (:ts2, :req2, :hash2, 'block', 'PII detected', '[{"rule_id": "pii_ssn_us", "position": 0}]', 15.2),
            (:ts3, :req3, :hash3, 'allow', 'Clean prompt', '[]', 8.7)
        """,
        {
            "ts1": start_of_day,
            "req1": "550e8400-e29b-41d4-a716-446655440000",
            "hash1": "abc123",
            "ts2": start_of_day,
            "req2": "550e8400-e29b-41d4-a716-446655440001",
            "hash2": "def456",
            "ts3": start_of_day,
            "req3": "550e8400-e29b-41d4-a716-446655440002",
            "hash3": "ghi789"
        }
    )
    await session.commit()
    
    response = client.get("/api/v1/metrics/summary", headers=test_auth_headers)
    assert response.status_code == 200
    
    data = response.json()
    assert data["rps"] > 0
    assert data["block_rate"] == 33.33  # 1 block out of 3 total
    assert data["avg_latency"] == 11.47  # (10.5 + 15.2 + 8.7) / 3
    assert data["cache_hit_rate"] == 0.0  # No cache data


@pytest.mark.asyncio
async def test_get_metrics_summary_unauthorized(client: TestClient):
    """Test metrics summary endpoint without auth"""
    response = client.get("/api/v1/metrics/summary")
    assert response.status_code == 401