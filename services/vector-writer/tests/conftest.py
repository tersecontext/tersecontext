# services/vector-writer/tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_redis():
    m = AsyncMock()
    m.ping.return_value = True
    return m


@pytest.fixture
def mock_writer():
    w = AsyncMock()
    w.get_collections.return_value = MagicMock(collections=[])
    return w


@pytest.fixture
def client(mock_redis, mock_writer):
    async def _noop(writer):
        """Replace consumer coroutines so they return immediately."""
        return

    with patch("app.writer.QdrantWriter", return_value=mock_writer), \
         patch("app.consumer.run_embedded_consumer", _noop), \
         patch("app.consumer.run_delete_consumer", _noop), \
         patch("app.main._get_redis", return_value=mock_redis):
        from app.main import app
        import app.main as main_mod
        main_mod._writer = mock_writer
        with TestClient(app) as c:
            yield c
