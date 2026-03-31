# services/symbol-resolver/tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_redis():
    """Async Redis mock for the FastAPI app (aioredis client)."""
    m = AsyncMock()
    m.ping.return_value = True
    return m


@pytest.fixture
def mock_driver():
    """Sync Neo4j driver mock."""
    d = MagicMock()
    d.verify_connectivity.return_value = None
    return d


@pytest.fixture
def client(mock_redis, mock_driver):
    with patch("app.main._get_redis", return_value=mock_redis), \
         patch("app.main.make_driver", return_value=mock_driver), \
         patch("app.main.run_consumer", new_callable=AsyncMock) as mock_consumer:
        # Make the consumer coroutine hang until cancelled
        async def _hang(*args, **kwargs):
            import asyncio
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
        mock_consumer.side_effect = _hang
        import app.main as main_mod
        # Ensure the module-level _driver is set by lifespan
        from app.main import app
        with TestClient(app) as c:
            yield c
        # Reset module state
        main_mod._redis_client = None
        main_mod._driver = None
