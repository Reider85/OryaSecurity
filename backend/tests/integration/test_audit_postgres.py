from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.audit import query_events, write_event
from app.db.session import get_sessionmaker, init_db, init_engine
from sqlalchemy import text


def _postgres_available() -> bool:
    """Check if a real PostgreSQL is reachable, without failing the suite."""
    if os.getenv("SCANNER_SKIP_DB_TESTS") == "1":
        return False

    async def check() -> bool:
        try:
            engine = init_engine()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            return False
        return True

    try:
        return asyncio.run(check())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(), reason="PostgreSQL is not available"
)


class TestAuditPostgresIntegration:
    @pytest.fixture(autouse=True)
    async def _prepare(self):
        init_engine()
        await init_db()
        yield

    async def test_write_event_persists_row(self) -> None:
        request_id = uuid.uuid4()
        tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"

        event = await write_event(
            request_id=str(request_id),
            prompt_hash="a" * 64,
            verdict="block",
            reason="Blocked by rules: pii_ssn",
            rules_matched=[{"rule_id": "pii_ssn", "position": [3, 9]}],
            tenant_id=tenant_id,
            prompt_text_redacted="SSN <SSN_HASH_abcd1234>",
            latency_ms=1.5,
        )

        assert event is not None
        assert event.id is not None

        factory = get_sessionmaker()
        async with factory() as session:
            result = await session.execute(
                text(
                    "SELECT request_id, prompt_hash, verdict, rules_matched, ts "
                    "FROM audit_events WHERE id = :id"
                ),
                {"id": event.id},
            )
            row = result.mappings().one()

        assert row["request_id"] == request_id
        assert row["prompt_hash"] == "a" * 64
        assert row["verdict"] == "block"
        assert row["rules_matched"] == [{"rule_id": "pii_ssn", "position": [3, 9]}]
        assert row["ts"] is not None

    async def test_query_events_filters(self) -> None:
        tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
        prompt_hash = "b" * 64

        await write_event(
            request_id=uuid.uuid4(),
            prompt_hash=prompt_hash,
            verdict="allow",
            tenant_id=tenant_id,
        )
        await write_event(
            request_id=uuid.uuid4(),
            prompt_hash="c" * 64,
            verdict="block",
            tenant_id=f"other-{uuid.uuid4().hex[:8]}",
        )

        by_tenant = await query_events(tenant_id=tenant_id)
        assert len(by_tenant) == 1
        assert by_tenant[0]["tenant_id"] == tenant_id
        assert by_tenant[0]["verdict"] == "allow"

        by_verdict = await query_events(verdict="block")
        assert all(row["verdict"] == "block" for row in by_verdict)
        assert by_verdict

        by_hash = await query_events(prompt_hash=prompt_hash)
        assert len(by_hash) == 1
        assert by_hash[0]["prompt_hash"] == prompt_hash

    async def test_query_events_time_window(self) -> None:
        await write_event(request_id=uuid.uuid4(), prompt_hash="d" * 64, verdict="allow")

        now = datetime.now(timezone.utc)
        inside = await query_events(start_ts=now - timedelta(hours=1), end_ts=now + timedelta(hours=1))
        outside = await query_events(start_ts=now - timedelta(days=7), end_ts=now - timedelta(days=1))

        assert inside
        assert not outside

    async def test_query_events_pagination(self) -> None:
        tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
        for _ in range(3):
            await write_event(
                request_id=uuid.uuid4(),
                prompt_hash="e" * 64,
                verdict="allow",
                tenant_id=tenant_id,
            )

        page = await query_events(tenant_id=tenant_id, limit=2, offset=0)
        next_page = await query_events(tenant_id=tenant_id, limit=2, offset=2)

        assert len(page) == 2
        assert len(next_page) == 1

    async def test_default_ts_and_policy_version(self) -> None:
        event = await write_event(
            request_id=uuid.uuid4(),
            prompt_hash="f" * 64,
            verdict="allow",
        )

        assert event is not None
        factory = get_sessionmaker()
        async with factory() as session:
            result = await session.execute(
                text("SELECT ts, policy_version FROM audit_events WHERE id = :id"),
                {"id": event.id},
            )
            row = result.mappings().one()

        assert row["ts"] is not None
        assert row["policy_version"] is not None

    async def test_invalid_verdict_rejected_by_constraint(self) -> None:
        factory = get_sessionmaker()
        with pytest.raises(Exception):
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO audit_events (request_id, prompt_hash, verdict) "
                        "VALUES (:rid, :ph, 'maybe')"
                    ),
                    {"rid": uuid.uuid4(), "ph": "0" * 64},
                )
                await session.commit()
