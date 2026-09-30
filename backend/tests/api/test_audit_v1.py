import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from uuid import uuid4

from app.main import app
from app.db.session import get_session
from app.core.auth import verify_api_key
from app.models.audit import AuditEvent, RuleMatch
from tests.conftest import test_auth_headers


@pytest.mark.asyncio
async def test_get_audit_events_default(client: TestClient, session: AsyncSession):
    """Test audit events endpoint with default parameters"""
    # Create test audit events
    now = datetime.utcnow()
    events = []
    
    for i in range(15):
        event = AuditEvent(
            ts=now - timedelta(minutes=i),
            request_id=uuid4(),
            prompt_hash=f"hash{i:03d}",
            prompt_text_redacted=f"redacted_prompt_{i}",
            verdict="allow" if i % 2 == 0 else "block",
            reason="Clean" if i % 2 == 0 else "PII detected",
            rules_matched=[RuleMatch(rule_id="test_rule", position=i)],
            latency_ms=10.0 + i
        )
        events.append(event)
    
    # Insert test data
    for event in events:
        await session.execute(
            """
            INSERT INTO audit_events (ts, request_id, prompt_hash, prompt_text_redacted, verdict, reason, rules_matched, latency_ms)
            VALUES (:ts, :req_id, :hash, :redacted, :verdict, :reason, :rules, :latency)
            """,
            {
                "ts": event.ts,
                "req_id": str(event.request_id),
                "hash": event.prompt_hash,
                "redacted": event.prompt_text_redacted,
                "verdict": event.verdict,
                "reason": event.reason,
                "rules": str([{"rule_id": rm.rule_id, "position": rm.position} for rm in event.rules_matched]),
                "latency": event.latency_ms
            }
        )
    await session.commit()
    
    # Test default request (limit 10, page 1)
    response = client.get("/api/v1/audit", headers=test_auth_headers)
    assert response.status_code == 200
    
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "per_page" in data
    
    assert len(data["items"]) == 10  # Default limit
    assert data["total"] == 15
    assert data["page"] == 1
    assert data["per_page"] == 10
    
    # Should be sorted by timestamp (newest first)
    assert data["items"][0]["ts"] == now.isoformat()
    assert data["items"][9]["ts"] == (now - timedelta(minutes=9)).isoformat()


@pytest.mark.asyncio
async def test_get_audit_events_with_filters(client: TestClient, session: AsyncSession):
    """Test audit events endpoint with filters"""
    # Create test data
    now = datetime.utcnow()
    
    # Insert allow events
    for i in range(5):
        await session.execute(
            """
            INSERT INTO audit_events (ts, request_id, prompt_hash, prompt_text_redacted, verdict, reason, rules_matched)
            VALUES (:ts, :req_id, :hash, :redacted, 'allow', 'Clean', '[]')
            """,
            {
                "ts": now - timedelta(minutes=i),
                "req_id": str(uuid4()),
                "hash": f"allow_hash{i:03d}",
                "redacted": f"allow_prompt_{i}"
            }
        )
    
    # Insert block events
    for i in range(5):
        await session.execute(
            """
            INSERT INTO audit_events (ts, request_id, prompt_hash, prompt_text_redacted, verdict, reason, rules_matched)
            VALUES (:ts, :req_id, :hash, :redacted, 'block', 'PII', '[{"rule_id": "test_rule", "position": 0}]')
            """,
            {
                "ts": now - timedelta(minutes=i + 10),
                "req_id": str(uuid4()),
                "hash": f"block_hash{i:03d}",
                "redacted": f"block_prompt_{i}"
            }
        )
    
    await session.commit()
    
    # Test verdict filter
    response = client.get("/api/v1/audit?verdict=allow&limit=3", headers=test_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 3
    assert all(item["verdict"] == "allow" for item in data["items"])
    
    # Test limit parameter
    response = client.get("/api/v1/audit?limit=2", headers=test_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_get_audit_events_unauthorized(client: TestClient):
    """Test audit events endpoint without auth"""
    response = client.get("/api/v1/audit")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_audit_events_pagination(client: TestClient, session: AsyncSession):
    """Test audit events endpoint pagination"""
    # Create 25 test events
    now = datetime.utcnow()
    
    for i in range(25):
        await session.execute(
            """
            INSERT INTO audit_events (ts, request_id, prompt_hash, prompt_text_redacted, verdict, reason, rules_matched)
            VALUES (:ts, :req_id, :hash, :redacted, 'allow', 'Clean', '[]')
            """,
            {
                "ts": now - timedelta(minutes=i),
                "req_id": str(uuid4()),
                "hash": f"hash{i:03d}",
                "redacted": f"prompt_{i}"
            }
        )
    
    await session.commit()
    
    # Test page 2
    response = client.get("/api/v1/audit?limit=10&page=2", headers=test_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 2
    assert data["per_page"] == 10
    assert len(data["items"]) == 10
    
    # Test page 3 (should have only 5 items)
    response = client.get("/api/v1/audit?limit=10&page=3", headers=test_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 3
    assert len(data["items"]) == 5


@pytest.mark.asyncio
async def test_get_audit_events_prompt_hash_search(client: TestClient, session: AsyncSession):
    """Test audit events endpoint with prompt_hash LIKE search"""
    # Create test events with different hash prefixes
    now = datetime.utcnow()
    
    # Events with "abc" prefix
    for i in range(5):
        await session.execute(
            """
            INSERT INTO audit_events (ts, request_id, prompt_hash, prompt_text_redacted, verdict, reason, rules_matched)
            VALUES (:ts, :req_id, :hash, :redacted, 'allow', 'Clean', '[]')
            """,
            {
                "ts": now - timedelta(minutes=i),
                "req_id": str(uuid4()),
                "hash": f"abc{i:03d}",
                "redacted": f"prompt_{i}"
            }
        )
    
    # Events with "xyz" prefix
    for i in range(3):
        await session.execute(
            """
            INSERT INTO audit_events (ts, request_id, prompt_hash, prompt_text_redacted, verdict, reason, rules_matched)
            VALUES (:ts, :req_id, :hash, :redacted, 'block', 'PII', '[]')
            """,
            {
                "ts": now - timedelta(minutes=i + 10),
                "req_id": str(uuid4()),
                "hash": f"xyz{i:03d}",
                "redacted": f"prompt_{i}"
            }
        )
    
    await session.commit()
    
    # Test search for "abc" prefix
    response = client.get("/api/v1/audit?prompt_hash=abc&limit=10", headers=test_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["items"]) == 5
    # All returned events should have "abc" prefix
    assert all(item["prompt_hash"].startswith("abc") for item in data["items"])
    
    # Test search for "xy" prefix (should match "xyz" prefix)
    response = client.get("/api/v1/audit?prompt_hash=xy&limit=10", headers=test_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["items"]) == 3
    # All returned events should have "xyz" prefix
    assert all(item["prompt_hash"].startswith("xyz") for item in data["items"])
    
    # Test search for non-existent prefix
    response = client.get("/api/v1/audit?prompt_hash=nonexistent&limit=10", headers=test_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert len(data["items"]) == 0