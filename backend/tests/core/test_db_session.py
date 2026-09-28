from __future__ import annotations

import os

import pytest

from app.db import session as db_session


class FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        return False


class FakeConn:
    def __init__(self, executed):
        self.executed = executed

    async def execute(self, sql):
        self.executed.append(sql)


class FakePool:
    def __init__(self):
        self.executed: list[str] = []
        self.conn = FakeConn(self.executed)

    def acquire(self):
        return FakeAcquire(self.conn)

    async def close(self):
        pass


class FakeORM:
    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.closed = False

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def close(self):
        self.closed = True


class TestEngineLifecycle:
    def setup_method(self) -> None:
        db_session._engine = None
        db_session._sessionmaker = None

    def teardown_method(self) -> None:
        engine = db_session._engine
        db_session._engine = None
        db_session._sessionmaker = None
        if engine is not None:
            engine.sync_engine.dispose()

    async def test_init_engine_is_idempotent(self) -> None:
        first = db_session.init_engine()
        second = db_session.init_engine()
        assert first is second
        assert db_session.get_sessionmaker() is not None

    async def test_get_session_commits_and_closes(self) -> None:
        orm = FakeORM()
        monkey_session = lambda *args, **kwargs: orm  # noqa: E731
        db_session._sessionmaker = monkey_session

        async with db_session.get_session() as session:
            assert session is orm

        assert orm.committed is True
        assert orm.rolled_back is False
        assert orm.closed is True

    async def test_get_session_rolls_back_on_error(self) -> None:
        orm = FakeORM()
        db_session._sessionmaker = lambda *args, **kwargs: orm

        with pytest.raises(RuntimeError):
            async with db_session.get_session():
                raise RuntimeError("boom")

        assert orm.rolled_back is True
        assert orm.committed is False
        assert orm.closed is True

    async def test_dispose_engine_resets_state(self) -> None:
        engine = db_session.init_engine()
        assert db_session._sessionmaker is not None

        await db_session.dispose_engine()

        assert db_session._engine is None
        assert db_session._sessionmaker is None
        assert engine is not None

    async def test_dispose_engine_when_not_initialized(self) -> None:
        await db_session.dispose_engine()
        assert db_session._engine is None

    async def test_get_sessionmaker_initializes_engine(self) -> None:
        factory = db_session.get_sessionmaker()
        assert factory is db_session.get_sessionmaker()
        assert db_session._engine is not None


class TestMigrations:
    def teardown_method(self) -> None:
        db_session._pool = None

    def test_migrations_dir_contains_expected_files(self) -> None:
        directory = db_session._migrations_dir()
        assert os.path.isdir(directory)
        files = sorted(os.listdir(directory))
        assert "001_api_keys.sql" in files
        assert "002_audit_events.sql" in files

    async def test_init_db_applies_all_migrations_in_order(self, monkeypatch) -> None:
        pool = FakePool()
        db_session._pool = pool

        await db_session.init_db()

        assert len(pool.executed) == 2
        assert "api_keys" in pool.executed[0]
        assert "audit_events" in pool.executed[1]
        assert "ix_audit_events_tenant_ts" in pool.executed[1]
        assert "ix_audit_events_prompt_hash" in pool.executed[1]
        assert "ix_audit_events_verdict" in pool.executed[1]

    async def test_migrations_are_idempotent(self) -> None:
        for filename in os.listdir(db_session._migrations_dir()):
            if not filename.endswith(".sql"):
                continue
            path = os.path.join(db_session._migrations_dir(), filename)
            with open(path, "r") as f:
                sql = f.read()
            assert "CREATE TABLE IF NOT EXISTS" in sql
            assert "CREATE INDEX IF NOT EXISTS" in sql

    async def test_close_db_disposes_pool_and_engine(self) -> None:
        db_session._pool = FakePool()
        db_session.init_engine()

        await db_session.close_db()

        assert db_session._pool is None
        assert db_session._engine is None
