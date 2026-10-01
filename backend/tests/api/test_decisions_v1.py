import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from uuid import uuid4

from app.main import app
from app.db.session import get_session
from app.core.auth import verify_api_key
from app.models.audit import AuditEvent
from tests.conftest import test_auth_headers


@pytest.mark.asyncio
async def test_get_decisions_default(client: TestClient, session: AsyncSession):
    """Test decisions endpoint with default parameters"""
    # Create test audit events (which serve as decisions)
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
            rules_matched=[{"rule_id": "test_rule", "position": [i, i+5]}],
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
                "rules": str([{"rule_id": rm["rule_id"], "position": rm["position"]} for rm in event.rules_matched]),
                "latency": event.latency_ms
            }
        )
    await session.commit()
    
    # Test default request (limit 50, page 1)
    response = client.get("/api/v1/decisions", headers=test_auth_headers)
    assert response.status_code == 200
    
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "per_page" in data
    assert "has_next" in data
    assert "has_prev" in data
    
    assert len(data["items"]) == 15  # All events
    assert data["total"] == 15
    assert data["page"] == 1
    assert data["per_page"] == 50
    assert data["has_next"] is False
    assert data["has_prev"] is False
    
    # Check decision-specific fields
    for item in data["items"]:
        assert "request_id" in item
        assert "tenant_id" in item
        assert "prompt_hash" in item
        assert "prompt_text_redacted" in item
        assert "verdict" in item
        assert "reason" in item
        assert "rules_matched" in item
        assert "policy_version" in item
        assert "cache_status" in item
        assert "latency_breakdown" in item
        assert "created_at" in item
        assert "updated_at" in item
        
        # Check latency breakdown structure
        latency_breakdown = item["latency_breakdown"]
        assert "fast_path_ms" in latency_breakdown
        assert "slow_path_ms" in latency_breakdown
        assert "pdp_ms" in latency_breakdown
        assert "audit_ms" in latency_breakdown
        assert "total_ms" in latency_breakdown
        
        # Check rules matched structure
        for rule in item["rules_matched"]:
            assert "rule_id" in rule
            assert "rule_name" in rule
            assert "severity" in rule
            assert "action" in rule
            assert "position" in rule
            assert "matched_value" in rule


@pytest.mark.asyncio
async def test_get_decisions_with_filters(client: TestClient, session: AsyncSession):
    """Test decisions endpoint with filters"""
    # Create test data
    now = datetime.utcnow()
    
    # Insert allow events
    for i in range(5):
        await session.execute(
            """
            INSERT INTO audit_events (ts, request_id, prompt_hash, prompt_text_redacted, verdict, reason, rules_matched, latency_ms)
            VALUES (:ts, :req_id, :hash, :redacted, 'allow', 'Clean', '[]', :latency)
            """,
            {
                "ts": now - timedelta(minutes=i),
                "req_id": str(uuid4()),
                "hash": f"allow_hash{i:03d}",
                "redacted": f"allow_prompt_{i}",
                "latency": 5.0 + i
            }
        )
    
    # Insert block events
    for i in range(5):
        await session.execute(
            """
            INSERT INTO audit_events (ts, request_id, prompt_hash, prompt_text_redacted, verdict, reason, rules_matched, latency_ms)
            VALUES (:ts, :req_id, :hash, :redacted, 'block', 'PII', '[{"rule_id": "test_rule", "position": [0, 5]}]', :latency)
            """,
            {
                "ts": now - timedelta(minutes=i + 10),
                "req_id": str(uuid4()),
                "hash": f"block_hash{i:03d}",
                "redacted": f"block_prompt_{i}",
                "latency": 15.0 + i
            }
        )
    
    await session.commit()
    
    # Test verdict filter
    response = client.get("/api/v1/decisions?verdict=allow&limit=3", headers=test_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 3
    assert all(item["verdict"] == "allow" for item in data["items"])
    
    # Test limit parameter
    response = client.get("/api/v1/decisions?limit=2", headers=test_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_get_decisions_pagination(client: TestClient, session: AsyncSession):
    """Test decisions endpoint pagination"""
    # Create 25 test events
    now = datetime.utcnow()
    
    for i in range(25):
        await session.execute(
            """
            INSERT INTO audit_events (ts, request_id, prompt_hash, prompt_text_redacted, verdict, reason, rules_matched, latency_ms)
            VALUES (:ts, :req_id, :hash, :redacted, 'allow', 'Clean', '[]', :latency)
            """,
            {
                "ts": now - timedelta(minutes=i),
                "req_id": str(uuid4()),
                "hash": f"hash{i:03d}",
                "redacted": f"prompt_{i}",
                "latency": 10.0 + i
            }
        )
    
    await session.commit()
    
    # Test page 1 with limit 10
    response = client.get("/api/v1/decisions?limit=10&page=1", headers=test_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["per_page"] == 10
    assert len(data["items"]) == 10
    assert data["has_next"] is True
    assert data["has_prev"] is False
    
    # Test page 2
    response = client.get("/api/v1/decisions?limit=10&page=2", headers=test_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 2
    assert data["has_next"] is True
    assert data["has_prev"] is True
    
    # Test page 3 (should have only 5 items)
    response = client.get("/api/v1/decisions?limit=10&page=3", headers=test_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 3
    assert len(data["items"]) == 5
    assert data["has_next"] is False
    assert data["has_prev"] is True


@pytest.mark.asyncio
async def test_get_decisions_unauthorized(client: TestClient):
    """Test decisions endpoint without auth"""
    response = client.get("/api/v1/decisions")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_decision_detail(client: TestClient, session: AsyncSession):
    """Test getting a specific decision by request_id"""
    # Create a test audit event
    now = datetime.utcnow()
    request_id = str(uuid4())
    
    await session.execute(
        """
        INSERT INTO audit_events (ts, request_id, prompt_hash, prompt_text_redacted, verdict, reason, rules_matched, latency_ms)
        VALUES (:ts, :req_id, :hash, :redacted, 'block', 'PII detected', '[{"rule_id": "test_rule", "position": [0, 5]}]', :latency)
        """,
        {
            "ts": now,
            "req_id": request_id,
            "hash": "test_hash",
            "redacted": "redacted_prompt",
            "latency": 15.5
        }
    )
    await session.commit()
    
    # Test getting the specific decision
    response = client.get(f"/api/v1/decisions/{request_id}", headers=test_auth_headers)
    assert response.status_code == 200
    
    data = response.json()
    assert data["request_id"] == request_id
    assert data["verdict"] == "block"
    assert data["reason"] == "PII detected"
    assert data["prompt_text_redacted"] == "redacted_prompt"
    assert len(data["rules_matched"]) == 1
    assert data["rules_matched"][0]["rule_id"] == "test_rule"
    assert data["latency_breakdown"]["total_ms"] == 15.5


@pytest.mark.asyncio
async def test_get_decision_detail_not_found(client: TestClient, session: AsyncSession):
    """Test getting a non-existent decision"""
    # Use a UUID that doesn't exist
    fake_request_id = str(uuid4())
    
    response = client.get(f"/api/v1/decisions/{fake_request_id}", headers=test_auth_headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_decisions_date_range_filter(client: TestClient, session: AsyncSession):
    """Test decisions endpoint with date range filter"""
    now = datetime.utcnow()
    
    # Create events before the start date
    for i in range(3):
        await session.execute(
            """
            INSERT INTO audit_events (ts, request_id, prompt_hash, prompt_text_redacted, verdict, reason, rules_matched, latency_ms)
            VALUES (:ts, :req_id, :hash, :redacted, 'allow', 'Clean', '[]', :latency)
            """,
            {
                "ts": now - timedelta(days=2, minutes=i),
                "req_id": str(uuid4()),
                "hash": f"old_hash{i:03d}",
                "redacted": f"old_prompt_{i}",
                "latency": 5.0
            }
        )
    
    # Create events within the date range
    for i in range(5):
        await session.execute(
            """
            INSERT INTO audit_events (ts, request_id, prompt_hash, prompt_text_redacted, verdict, reason, rules_matched, latency_ms)
            VALUES (:ts, :req_id, :hash, :redacted, 'block', 'PII', '[{"rule_id": "test_rule", "position": [0, 5]}]', :latency)
            """,
            {
                "ts": now - timedelta(hours=1, minutes=i),
                "req_id": str(uuid4()),
                "hash": f"recent_hash{i:03d}",
                "redacted": f"recent_prompt_{i}",
                "latency": 10.0
            }
        )
    
    await session.commit()
    
    # Test date range filter (last 2 hours)
    start_date = (now - timedelta(hours=2)).isoformat()
    end_date = now.isoformat()
    
    response = client.get(f"/api/v1/decisions?start_date={start_date}&end_date={end_date}&limit=10", 
                        headers=test_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5  # Only recent events should match
    assert len(data["items"]) == 5


@pytest.mark.asyncio
async def test_cache_status_simulation(client: TestClient, session: AsyncSession):
    """Test that cache status is properly simulated based on latency"""
    now = datetime.utcnow()
    
    # Create fast event (should be cache HIT)
    fast_request_id = str(uuid4())
    await session.execute(
        """
        INSERT INTO audit_events (ts, request_id, prompt_hash, prompt_text_redacted, verdict, reason, rules_matched, latency_ms)
        VALUES (:ts, :req_id, :hash, :redacted, 'allow', 'Clean', '[]', 2.0)
        """,
        {
            "ts": now,
            "req_id": fast_request_id,
            "hash": "fast_hash",
            "redacted": "fast_prompt",
            "latency": 2.0
        }
    )
    
    # Create slow event (should be cache MISS)
    slow_request_id = str(uuid4())
    await session.execute(
        """
        INSERT INTO audit_events (ts, request_id, prompt_hash, prompt_text_redacted, verdict, reason, rules_matched, latency_ms)
        VALUES (:ts, :req_id, :hash, :redacted, 'block', 'PII', '[{"rule_id": "test_rule", "position": [0, 5]}]', 15.0)
        """,
        {
            "ts": now,
            "req_id": slow_request_id,
            "hash": "slow_hash",
            "redacted": "slow_prompt",
            "latency": 15.0
        }
    )
    
    await session.commit()
    
    # Test fast event (should have cache HIT)
    response = client.get(f"/api/v1/decisions/{fast_request_id}", headers=test_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["cache_status"] == "HIT"
    
    # Test slow event (should have cache MISS)
    response = client.get(f"/api/v1/decisions/{slow_request_id}", headers=test_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["cache_status"] == "MISS"