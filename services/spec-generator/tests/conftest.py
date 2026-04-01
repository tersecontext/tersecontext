import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure required env vars are set before importing app modules
os.environ.setdefault("POSTGRES_DSN", "postgres://test:test@localhost/test")
os.environ.setdefault("NEO4J_PASSWORD", "test")


@pytest.fixture
def mock_redis():
    m = AsyncMock()
    m.ping.return_value = True
    return m


@pytest.fixture
def mock_store():
    store = MagicMock()
    store.ensure_schema = AsyncMock()
    store.ensure_collection = AsyncMock()
    store.close = AsyncMock()
    store.upsert_spec = AsyncMock()
    store.upsert_qdrant = AsyncMock()
    # For ready endpoint: postgres pool
    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=1)
    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=mock_pool)
    mock_pool.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.__aexit__ = AsyncMock(return_value=False)
    store._get_pool = AsyncMock(return_value=mock_pool)
    # For ready endpoint: qdrant
    store._qdrant = MagicMock()
    store._qdrant.get_collections = MagicMock(return_value=MagicMock(collections=[]))
    return store


@pytest.fixture
def client(mock_redis, mock_store):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _noop_lifespan(app):
        yield

    import app.main as main_mod

    original_store = main_mod._store
    original_lifespan = main_mod.app.router.lifespan_context
    original_checkers = list(main_mod._svc._dep_checkers)

    main_mod._store = mock_store
    main_mod.app.router.lifespan_context = _noop_lifespan

    # Inject dep_checkers that use the mock_redis/mock_store so /ready works
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
    main_mod._svc.add_dep_checker(check_redis, name="redis")
    main_mod._svc.add_dep_checker(check_postgres, name="postgres")
    main_mod._svc.add_dep_checker(check_qdrant, name="qdrant")

    with patch.object(main_mod._svc, "get_redis", return_value=mock_redis):
        from fastapi.testclient import TestClient
        with TestClient(main_mod.app) as c:
            yield c

    main_mod._store = original_store
    main_mod.app.router.lifespan_context = original_lifespan
    main_mod._svc._dep_checkers.clear()
    for checker_fn, dep_name, dep_required in original_checkers:
        main_mod._svc.add_dep_checker(checker_fn, name=dep_name, required=dep_required)
