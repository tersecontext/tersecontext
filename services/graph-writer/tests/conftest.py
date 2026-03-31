# services/graph-writer/tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_redis():
    m = AsyncMock()
    m.ping.return_value = True
    m.xreadgroup.return_value = []
    return m


@pytest.fixture
def mock_driver():
    d = MagicMock()
    d.verify_connectivity.return_value = None
    return d


@pytest.fixture
def client(mock_redis, mock_driver):
    # Patch _get_redis and _make_driver so no real infra is needed.
    # Also patch the consumer coroutines so they return immediately
    # instead of running infinite loops.
    async def _noop_consumer(driver):
        return

    with patch("app.main._get_redis", return_value=mock_redis), \
         patch("app.main._make_driver", return_value=mock_driver), \
         patch("app.consumer.run_edge_consumer", side_effect=_noop_consumer), \
         patch("app.consumer.run_node_consumer", side_effect=_noop_consumer):
        from app.main import app
        with TestClient(app) as c:
            yield c
