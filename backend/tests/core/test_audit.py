from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from structlog.testing import capture_logs
from sqlalchemy.dialects import postgresql

from app.config import settings
from app.core import audit
from app.core.audit import _coerce_request_id, _normalize_rules, write_event, query_events
from app.db.models import AuditEvent


class FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return FakeScalarResult(self._rows)


class FakeSession:
    """Minimal stand-in for AsyncSession."""

    def __init__(self, rows=None, fail_on_flush=False):
        self.added: list[AuditEvent] = []
        self.executed: list = []
        self.closed = False
        self._rows = rows or []
        self._fail_on_flush = fail_on_flush

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        if self._fail_on_flush:
            raise RuntimeError("connection refused")

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def close(self):
        self.closed = True

    async def execute(self, stmt):
        self.executed.append(stmt)
        return FakeResult(self._rows)


def install_session(monkeypatch, session: FakeSession) -> FakeSession:
    @asynccontextmanager
    async def fake_get_session():
        yield session

    monkeypatch.setattr(audit, "get_session", fake_get_session)
    return session


def make_event(**overrides) -> AuditEvent:
    defaults = dict(
        id=1,
        ts=datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc),
        request_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        tenant_id="tenant-a",
        prompt_hash="a" * 64,
        prompt_text_redacted="hello <EMAIL_HASH_abcd1234>",
        verdict="allow",
        reason="no rules matched",
        rules_matched=[{"rule_id": "pii_email", "position": [0, 5]}],
        policy_version="mvp-1.0",
        latency_ms=1.25,
    )
    defaults.update(overrides)
    return AuditEvent(**defaults)


def compiled(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


class TestModel:
    def test_table_name(self) -> None:
        assert AuditEvent.__tablename__ == "audit_events"

    def test_column_types(self) -> None:
        columns = AuditEvent.__table__.columns
        pg = postgresql.dialect()
        assert columns["id"].type.compile(dialect=pg) == "BIGINT"
        assert "WITH TIME ZONE" in columns["ts"].type.compile(dialect=pg)
        assert columns["tenant_id"].type.length == 64
        assert columns["prompt_hash"].type.length == 64
        assert columns["verdict"].type.length == 16
        assert columns["policy_version"].type.length == 16
        assert columns["rules_matched"].type.compile(dialect=pg) == "JSONB"

    def test_required_columns(self) -> None:
        columns = AuditEvent.__table__.columns
        for name in ("ts", "request_id", "prompt_hash", "verdict", "rules_matched"):
            assert not columns[name].nullable, name
        for name in ("tenant_id", "prompt_text_redacted", "reason", "policy_version", "latency_ms"):
            assert columns[name].nullable, name

    def test_indexes(self) -> None:
        index_names = {idx.name for idx in AuditEvent.__table__.indexes}
        assert index_names == {
            "ix_audit_events_tenant_ts",
            "ix_audit_events_prompt_hash",
            "ix_audit_events_verdict",
        }

    def test_verdict_check_constraint(self) -> None:
        constraints = {c.name for c in AuditEvent.__table__.constraints}
        assert "ck_audit_events_verdict" in constraints

    def test_repr(self) -> None:
        event = make_event()
        assert "AuditEvent(" in repr(event)
        assert "verdict='allow'" in repr(event)


class TestHelpers:
    def test_coerce_request_id_from_uuid(self) -> None:
        value = uuid.uuid4()
        assert _coerce_request_id(value) is value

    def test_coerce_request_id_from_string(self) -> None:
        value = uuid.uuid4()
        assert _coerce_request_id(str(value)) == value

    def test_coerce_request_id_invalid(self) -> None:
        generated = _coerce_request_id("not-a-uuid")
        assert isinstance(generated, uuid.UUID)

    def test_coerce_request_id_none(self) -> None:
        assert isinstance(_coerce_request_id(None), uuid.UUID)

    def test_normalize_rules_keeps_only_id_and_position(self) -> None:
        rules = [
            {
                "rule_id": "pii_email",
                "rule_name": "Email",
                "value_hash": "deadbeef",
                "position": (4, 20),
                "severity": "high",
                "action": "block",
            }
        ]
        assert _normalize_rules(rules) == [{"rule_id": "pii_email", "position": [4, 20]}]

    def test_normalize_rules_empty(self) -> None:
        assert _normalize_rules(None) == []
        assert _normalize_rules([]) == []

    def test_normalize_rules_missing_position(self) -> None:
        assert _normalize_rules([{"rule_id": "aws_key"}]) == [
            {"rule_id": "aws_key", "position": []}
        ]


class TestWriteEvent:
    async def test_writes_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = install_session(monkeypatch, FakeSession())
        request_id = uuid.uuid4()

        event = await write_event(
            request_id=str(request_id),
            prompt_hash="b" * 64,
            verdict="block",
            reason="Blocked by rules: pii_ssn",
            rules_matched=[{"rule_id": "pii_ssn", "position": [10, 20]}],
            tenant_id="tenant-x",
            prompt_text_redacted="SSN <SSN_HASH_1234abcd>",
            latency_ms=2.5,
        )

        assert event is not None
        assert len(session.added) == 1
        written = session.added[0]
        assert written.request_id == request_id
        assert written.prompt_hash == "b" * 64
        assert written.verdict == "block"
        assert written.tenant_id == "tenant-x"
        assert written.prompt_text_redacted == "SSN <SSN_HASH_1234abcd>"
        assert written.rules_matched == [{"rule_id": "pii_ssn", "position": [10, 20]}]
        assert written.latency_ms == 2.5
        assert written.policy_version == settings.policy_version

    async def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = install_session(monkeypatch, FakeSession())

        event = await write_event(
            request_id=uuid.uuid4(),
            prompt_hash="c" * 64,
            verdict="allow",
        )

        assert event is not None
        written = session.added[0]
        assert written.tenant_id is None
        assert written.reason is None
        assert written.prompt_text_redacted is None
        assert written.latency_ms is None
        assert written.rules_matched == []

    async def test_explicit_policy_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_session(monkeypatch, FakeSession())

        event = await write_event(
            request_id=uuid.uuid4(),
            prompt_hash="d" * 64,
            verdict="allow",
            policy_version="1.0.0",
        )

        assert event is not None
        assert event.policy_version == "1.0.0"

    async def test_invalid_request_id_is_replaced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_session(monkeypatch, FakeSession())

        event = await write_event(
            request_id="abc",
            prompt_hash="e" * 64,
            verdict="allow",
        )

        assert event is not None
        assert isinstance(event.request_id, uuid.UUID)

    async def test_fails_open_on_db_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_session(monkeypatch, FakeSession(fail_on_flush=True))

        result = await write_event(
            request_id=uuid.uuid4(),
            prompt_hash="f" * 64,
            verdict="allow",
        )

        assert result is None

    async def test_disabled_audit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = install_session(monkeypatch, FakeSession())
        monkeypatch.setattr(settings, "audit_enabled", False)

        with capture_logs() as events:
            result = await write_event(
                request_id=uuid.uuid4(),
                prompt_hash="0" * 64,
                verdict="allow",
            )

        assert result is None
        assert session.added == []
        assert any(e["event"] == "audit_disabled" for e in events)

    async def test_structlog_event_emitted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_session(monkeypatch, FakeSession())

        with capture_logs() as events:
            await write_event(
                request_id=uuid.uuid4(),
                prompt_hash="1" * 64,
                verdict="allow",
                metadata={"model": "gpt-test"},
            )

        audit_logs = [e for e in events if e["event"] == "audit_event"]
        assert len(audit_logs) == 1
        assert audit_logs[0]["verdict"] == "allow"
        assert audit_logs[0]["latency_ms"] == 0.0
        assert audit_logs[0]["rules_matched_count"] == 0
        assert audit_logs[0]["metadata"] == {"model": "gpt-test"}

    async def test_structlog_warning_on_db_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_session(monkeypatch, FakeSession(fail_on_flush=True))

        with capture_logs() as events:
            await write_event(
                request_id=uuid.uuid4(),
                prompt_hash="1" * 64,
                verdict="allow",
            )

        warnings = [e for e in events if e["event"] == "audit_write_failed"]
        assert len(warnings) == 1
        assert warnings[0]["fail_open"] is True


class TestQueryEvents:
    async def test_returns_serialized_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_session(monkeypatch, FakeSession(rows=[make_event()]))

        rows = await query_events()

        assert len(rows) == 1
        assert rows[0]["id"] == 1
        assert rows[0]["request_id"] == "11111111-1111-1111-1111-111111111111"
        assert rows[0]["verdict"] == "allow"
        assert rows[0]["rules_matched"] == [{"rule_id": "pii_email", "position": [0, 5]}]
        assert rows[0]["latency_ms"] == 1.25

    async def test_empty_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_session(monkeypatch, FakeSession(rows=[]))
        assert await query_events() == []

    async def test_tenant_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = install_session(monkeypatch, FakeSession())

        await query_events(tenant_id="tenant-a")

        sql = compiled(session.executed[0])
        assert "tenant_id" in sql
        assert "tenant-a" in sql

    async def test_verdict_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = install_session(monkeypatch, FakeSession())

        await query_events(verdict="block")

        sql = compiled(session.executed[0])
        assert "verdict" in sql
        assert "block" in sql

    async def test_prompt_hash_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = install_session(monkeypatch, FakeSession())

        await query_events(prompt_hash="9" * 64)

        assert "prompt_hash" in compiled(session.executed[0])

    async def test_time_range_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = install_session(monkeypatch, FakeSession())
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        end = datetime(2026, 9, 28, tzinfo=timezone.utc)

        await query_events(start_ts=start, end_ts=end)

        sql = compiled(session.executed[0])
        assert "ts >=" in sql
        assert "ts <=" in sql

    async def test_ordering_and_pagination(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = install_session(monkeypatch, FakeSession())

        await query_events(limit=10, offset=20)

        sql = compiled(session.executed[0])
        assert "ORDER BY audit_events.ts DESC" in sql
        assert "LIMIT" in sql
        assert "OFFSET" in sql

    async def test_all_filters_combined(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = install_session(monkeypatch, FakeSession())
        now = datetime.now(timezone.utc)

        await query_events(
            tenant_id="tenant-a",
            verdict="allow",
            prompt_hash="7" * 64,
            start_ts=now - timedelta(days=1),
            end_ts=now,
            limit=5,
        )

        sql = compiled(session.executed[0])
        for fragment in ("tenant_id", "verdict", "prompt_hash", "ts >=", "ts <="):
            assert fragment in sql
        assert "LIMIT" in sql

    async def test_row_serialization_tolerates_none_rules(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_session(monkeypatch, FakeSession(rows=[make_event(rules_matched=None)]))

        rows = await query_events()

        assert rows[0]["rules_matched"] == []


class TestWriteAuditEvent:
    def test_structlog_only_helper(self) -> None:
        with capture_logs() as events:
            audit.write_audit_event(
                request_id="rid",
                tenant_id="tenant-a",
                prompt_hash="2" * 64,
                verdict="block",
                reason="reason",
                rules_matched=[{"rule_id": "a"}, {"rule_id": "b"}],
                latency_ms=1.23456,
                metadata={"model": "gpt-test"},
            )

        event = next(e for e in events if e["event"] == "audit_event")
        assert event["rules_matched_count"] == 2
        assert event["latency_ms"] == 1.23
        assert event["metadata"] == {"model": "gpt-test"}
