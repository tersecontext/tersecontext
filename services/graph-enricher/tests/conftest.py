# services/graph-enricher/tests/conftest.py
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


def _make_noop_consumer():
    """Return an async callable that does nothing — replaces run_consumer."""
    async def _noop(driver):
        return
    return _noop


@pytest.fixture
def client(mock_redis, mock_driver):
    with patch("app.main._make_driver", return_value=mock_driver), \
         patch("app.main._svc.get_redis", return_value=mock_redis), \
         patch("app.consumer.run_consumer", new=_make_noop_consumer()):
        import app.main
        # Reset dep checkers so they don't accumulate across test runs
        app.main._svc._dep_checkers = []
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as c:
            yield c
