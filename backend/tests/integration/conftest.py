"""Integration test fixtures for the scanner pipeline.

These tests exercise the real pipeline end to end: FastAPI app, real rule
engine loaded from YAML, real Postgres for audit, and a Redis client.

Redis is served by ``fakeredis`` because a real Redis is not guaranteed to be
running locally. Set ``SCANNER_USE_TESTCONTAINERS=1`` to spin up real Postgres
and Redis containers via testcontainers instead (requires Docker).

Configuration
-------------
``SCANNER_TEST_DATABASE_URL``  DSN for the Postgres used by the tests.
``SCANNER_TEST_REDIS_URL``     DSN for Redis; forces the real client instead of
                               fakeredis when set.
``SCANNER_USE_TESTCONTAINERS`` ``1`` to use testcontainers instead of local
                               services.
``SCANNER_SKIP_DB_TESTS``      ``1`` to skip anything needing Postgres.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from types import SimpleNamespace
from typing import AsyncIterator, Callable, Iterator

import fakeredis
import fakeredis.aioredis
import httpx
import pytest
import pytest_asyncio



# The scanner loads its YAML rules from a CWD-relative directory. Integration
# tests must run from the backend/ root, so fail loudly rather than silently
# testing an empty rule set.
import app.core.pdp as pdp_module
from app.config import settings
from app.core.cache import decision_cache
from app.core.auth import _fingerprint_api_key, _hash_api_key
from app.db.session import close_db, init_db, init_engine
from app.main import app

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+asyncpg://scanner:scanner@127.0.0.1:5432/scanner"
)


def to_asyncpg_dsn(dsn: str) -> str:
    """Force the SQLAlchemy asyncpg driver.

    ``app.db.session.init_engine`` passes ``settings.database_url`` straight to
    ``create_async_engine``, so a bare ``postgresql://`` makes SQLAlchemy try to
    import the (absent) psycopg2 driver.
    """
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    if dsn.startswith("postgresql://"):
        return "postgresql+asyncpg://" + dsn[len("postgresql://") :]
    return dsn


def _use_testcontainers() -> bool:
    return os.getenv("SCANNER_USE_TESTCONTAINERS", "0") == "1"


def _test_database_url() -> str:
    return os.getenv("SCANNER_TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


async def _probe_postgres(dsn: str) -> bool:
    import asyncpg

    normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    try:
        conn = await asyncpg.connect(normalized, timeout=3)
    except Exception:
        return False
    await conn.close()
    return True


async def _probe_redis(url: str) -> bool:
    import redis.asyncio as redis_asyncio

    client = redis_asyncio.from_url(url)
    try:
        await client.ping()
    except Exception:
        return False
    finally:
        await client.aclose()
    return True


@pytest.fixture(scope="session")
def integration_services() -> Iterator[dict[str, str | None]]:
    """Resolve the Postgres/Redis endpoints used by the whole session."""
    if os.getenv("SCANNER_SKIP_DB_TESTS") == "1":
        pytest.skip("SCANNER_SKIP_DB_TESTS=1")

    if _use_testcontainers():
        try:
            from testcontainers.postgres import PostgresContainer
            from testcontainers.redis import RedisContainer
        except ImportError as exc:  # pragma: no cover - depends on env
            pytest.skip(f"testcontainers not available: {exc}")

        pg_container = PostgresContainer("postgres:16-alpine")
        pg_container.start()
        redis_container = RedisContainer("redis:7-alpine")
        redis_container.start()
        try:
            yield {
                "database_url": pg_container.get_connection_url().replace(
                    "psycopg2", "asyncpg"
                ),
                "redis_url": redis_container.get_container_host_ip()
                and f"redis://{redis_container.get_container_host_ip()}:"
                f"{redis_container.get_exposed_port(6379)}/0",
            }
        finally:
            pg_container.stop()
            redis_container.stop()
        return

    database_url = to_asyncpg_dsn(_test_database_url())
    redis_url = os.getenv("SCANNER_TEST_REDIS_URL")

    loop = asyncio.new_event_loop()
    try:
        pg_ok = loop.run_until_complete(_probe_postgres(database_url))
    finally:
        loop.close()

    if not pg_ok:
        pytest.skip(
            f"PostgreSQL is not reachable at {database_url}. "
            "Set SCANNER_TEST_DATABASE_URL or SCANNER_USE_TESTCONTAINERS=1."
        )

    yield {"database_url": database_url, "redis_url": redis_url}


@pytest.fixture(scope="session")
def rules_dir_is_loaded() -> None:
    """Guard against running with an empty rule set (wrong CWD)."""
    assert len(pdp_module.rule_set.list_rules()) > 0, (
        "No rules loaded. Run pytest from backend/ so that "
        f"settings.rules_dir ('{settings.rules_dir}') resolves."
    )


@pytest_asyncio.fixture(scope="session")
async def real_postgres(integration_services, rules_dir_is_loaded) -> AsyncIterator[None]:
    """Point the app at the integration Postgres and apply SQL migrations.

    The engine and pool are module-level globals in ``app.db.session``; the
    pytest-asyncio session loop scope keeps every consumer on one event loop so
    the asyncpg pool stays usable.
    """
    original_url = settings.database_url
    # Drop any engine/pool a previously collected module built against the
    # default DSN; init_engine() caches globally and would otherwise keep
    # pointing at the wrong role.
    await close_db()
    settings.database_url = str(integration_services["database_url"])
    init_engine()
    await init_db()
    try:
        yield
    finally:
        await close_db()
        settings.database_url = original_url


@pytest.fixture
def redis_client(integration_services, real_postgres) -> Iterator[object]:
    """Provide the Redis client the scanner should use.

    Prefers a real Redis when ``SCANNER_TEST_REDIS_URL`` points at one, otherwise
    falls back to fakeredis. Patches both ``app.db.redis_client`` (used by
    lifespan/startup and /health) and ``app.core.cache`` (which imported
    ``get_redis_connection`` by value).
    """
    import app.core.cache as cache_module
    import app.db.redis_client as redis_module

    real_url = integration_services["redis_url"]
    server = fakeredis.FakeServer()
    client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

    original_get_redis = redis_module.get_redis
    original_init_redis = redis_module.init_redis
    original_close_redis = redis_module.close_redis
    original_cache_conn = cache_module.get_redis_connection
    original_redis_url = settings.redis_url

    async def get_redis():
        return client

    async def init_redis():
        await client.ping()

    async def close_redis():
        await client.aclose()

    async def get_redis_connection():
        return client

    if real_url:
        import redis.asyncio as redis_asyncio

        real_client = redis_asyncio.from_url(real_url, decode_responses=True)

        async def get_redis():
            return real_client

        async def get_redis_connection():
            return real_client

        async def init_redis():
            await real_client.ping()

        async def close_redis():
            await real_client.aclose()

        client = real_client

    redis_module.get_redis = get_redis  # type: ignore[assignment]
    redis_module.init_redis = init_redis  # type: ignore[assignment]
    redis_module.close_redis = close_redis  # type: ignore[assignment]
    cache_module.get_redis_connection = get_redis_connection  # type: ignore[assignment]
    settings.redis_url = real_url or settings.redis_url

    try:
        yield client
    finally:
        redis_module.get_redis = original_get_redis  # type: ignore[assignment]
        redis_module.init_redis = original_init_redis  # type: ignore[assignment]
        redis_module.close_redis = original_close_redis  # type: ignore[assignment]
        cache_module.get_redis_connection = original_cache_conn  # type: ignore[assignment]
        settings.redis_url = original_redis_url


@pytest_asyncio.fixture
async def clean_audit(real_postgres) -> AsyncIterator[None]:
    """Empty the audit table so assertions see only this test's events."""
    from sqlalchemy import text

    from app.db.session import get_sessionmaker

    factory = get_sessionmaker()
    async with factory() as session:
        await session.execute(text("TRUNCATE TABLE audit_events RESTART IDENTITY"))
        await session.commit()
    try:
        yield
    finally:
        async with factory() as session:
            await session.execute(text("TRUNCATE TABLE audit_events RESTART IDENTITY"))
            await session.commit()


@pytest_asyncio.fixture
async def api_key_row(real_postgres) -> AsyncIterator[str]:
    """Insert an API key that ``authenticate_api_key`` can actually find.

    Authentication looks rows up by ``key_fingerprint`` (sha256 of the raw key),
    so the INSERT must populate it alongside the bcrypt hash.
    """
    from app.db.session import get_db_connection

    api_key = f"it-{secrets.token_urlsafe(24)}"
    fingerprint = _fingerprint_api_key(api_key)
    key_hash = _hash_api_key(api_key)

    async with get_db_connection() as conn:
        await conn.execute(
            "DELETE FROM api_keys WHERE key_fingerprint = $1", fingerprint
        )
        await conn.execute(
            """
            INSERT INTO api_keys (key_hash, key_fingerprint, tenant_id, active)
            VALUES ($1, $2, $3, TRUE)
            """,
            key_hash,
            fingerprint,
            "integration-tenant",
        )

    try:
        yield api_key
    finally:
        async with get_db_connection() as conn:
            await conn.execute(
                "DELETE FROM api_keys WHERE key_fingerprint = $1", fingerprint
            )


@pytest.fixture
def auth_headers(api_key_row: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key_row}"}


@pytest_asyncio.fixture
async def reset_cache(redis_client) -> AsyncIterator[None]:
    """Flush the decision cache between tests.

    ``DecisionCache.flush`` is a coroutine; existing suites call it without
    awaiting which silently leaks state.
    """
    await decision_cache.flush()
    try:
        yield
    finally:
        await decision_cache.flush()


async def _default_llm_handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    return httpx.Response(
        status_code=200,
        json={
            "id": "chatcmpl-integration",
            "object": "chat.completion",
            "created": 1700000000,
            "model": payload.get("model", "gpt-3.5-turbo"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        },
    )


@pytest_asyncio.fixture
async def llm_transport(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Callable]:
    """Install an ``httpx.MockTransport`` in place of the LLM provider call.

    Yields a callable that registers the handler for
    ``POST {settings.llm_provider_url}/v1/chat/completions``. Handlers receive
    the ``httpx.Request`` and return an ``httpx.Response``, so a test can assert
    on what the scanner forwarded, return a non-200, or raise
    ``httpx.ReadTimeout`` to exercise the 502 path.
    """
    import app.core.proxy as proxy_module

    current: dict[str, Callable] = {"handler": _default_llm_handler}

    def handler_for(request: httpx.Request) -> httpx.Response:
        return current["handler"](request)

    transport = httpx.MockTransport(handler_for)
    real_async_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.setdefault("transport", transport)
        return real_async_client(*args, **kwargs)

    # Patch only the proxy module's view of httpx so the scanner's own
    # TestClient traffic is unaffected.
    shim = SimpleNamespace(
        AsyncClient=factory,
        TimeoutException=httpx.TimeoutException,
        HTTPError=httpx.HTTPError,
    )
    monkeypatch.setattr(proxy_module, "httpx", shim)

    def configure(handler: Callable) -> None:
        current["handler"] = handler

    try:
        yield configure
    finally:
        current["handler"] = _default_llm_handler


@pytest_asyncio.fixture
async def app_lifespan(redis_client, real_postgres) -> AsyncIterator[None]:
    """Run the app's real lifespan (startup + shutdown) around a test.

    Startup applies migrations, pings Redis and bootstraps API keys, which is
    what production does, so /health and /metrics reflect real dependency state.
    """
    async with app.router.lifespan_context(app):
        yield


@pytest_asyncio.fixture
async def client(app_lifespan, reset_cache) -> AsyncIterator["httpx.AsyncClient"]:
    """Async HTTP client bound to the ASGI app on the current event loop.

    Using ``httpx.AsyncClient`` + ``ASGITransport`` rather than Starlette's
    ``TestClient`` keeps app code, Redis and the Postgres pool on one event
    loop. ``TestClient`` runs the app on its own portal loop, which deadlocks
    fakeredis and the asyncpg pool.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://scanner.test"
    ) as http_client:
        yield http_client


@pytest.fixture
def clean_audit_factory(client, clean_audit) -> None:
    """``clean_audit`` pulled in for side effects; keeps the dependency explicit."""
    return None