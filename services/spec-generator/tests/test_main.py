import asyncio
import os
from contextlib import asynccontextmanager, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("POSTGRES_DSN", "postgres://test:test@localhost/test")


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@contextmanager
def _patched_app(mock_redis, mock_store):
    """Context manager that patches the FastAPI app for testing with custom mocks.

    Injects dep_checkers that mirror the closures built in lifespan, but use
    mock_redis / mock_store so the /ready endpoint can return 503 on failures.
    """
    from fastapi.testclient import TestClient
    import app.main as main_mod

    original_store = main_mod._store
    original_lifespan = main_mod.app.router.lifespan_context
    original_checkers = list(main_mod._svc._dep_checkers)

    main_mod._store = mock_store
    main_mod.app.router.lifespan_context = _noop_lifespan

    # Build the same dep_checkers that lifespan() would register, using mocks
    async def check_redis() -> str | None:
        try:
            await mock_redis.ping()
            return None
        except Exception as exc:
            return f"redis: {exc}"

    async def check_postgres() -> str | None:
        try:
            if mock_store:
                pool = await mock_store._get_pool()
                async with pool.acquire() as conn:
                    await conn.fetchval("SELECT 1")
            return None
        except Exception as exc:
            return f"postgres: {exc}"

    async def check_qdrant() -> str | None:
        try:
            if mock_store:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, mock_store._qdrant.get_collections)
            return None
        except Exception as exc:
            return f"qdrant: {exc}"

    main_mod._svc._dep_checkers.clear()
    main_mod._svc._dep_checkers.extend([check_redis, check_postgres, check_qdrant])

    with patch.object(main_mod._svc, "get_redis", return_value=mock_redis):
        with TestClient(main_mod.app) as c:
            yield c

    main_mod._store = original_store
    main_mod.app.router.lifespan_context = original_lifespan
    main_mod._svc._dep_checkers.clear()
    main_mod._svc._dep_checkers.extend(original_checkers)


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "spec-generator"
    assert "version" in body


def test_ready_ok(client):
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_ready_503_redis_down(mock_store):
    bad_redis = AsyncMock()
    bad_redis.ping.side_effect = Exception("connection refused")

    with _patched_app(bad_redis, mock_store) as c:
        resp = c.get("/ready")

    assert resp.status_code == 503
    assert any("redis" in e for e in resp.json()["errors"])


def test_ready_503_postgres_down(mock_redis, mock_store):
    # Make postgres pool raise
    mock_store._get_pool = AsyncMock(side_effect=Exception("postgres unreachable"))

    with _patched_app(mock_redis, mock_store) as c:
        resp = c.get("/ready")

    assert resp.status_code == 503
    assert any("postgres" in e for e in resp.json()["errors"])


def test_ready_503_qdrant_down(mock_redis, mock_store):
    # Make qdrant raise
    mock_store._qdrant.get_collections.side_effect = Exception("qdrant unreachable")

    with _patched_app(mock_redis, mock_store) as c:
        resp = c.get("/ready")

    assert resp.status_code == 503
    assert any("qdrant" in e for e in resp.json()["errors"])


def test_metrics_returns_prometheus_format(client):
    import app.consumer as consumer

    # Set known counter values
    consumer.messages_processed_total = 42
    consumer.specs_written_total = 39
    consumer.specs_embedded_total = 38

    resp = client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text

    # Check for Prometheus exposition format
    assert "# HELP" in text
    assert "# TYPE" in text
    assert "spec_generator_messages_processed_total 42" in text
    assert "spec_generator_specs_written_total 39" in text
    assert "spec_generator_specs_embedded_total 38" in text

    # Reset counters
    consumer.messages_processed_total = 0
    consumer.specs_written_total = 0
    consumer.specs_embedded_total = 0
