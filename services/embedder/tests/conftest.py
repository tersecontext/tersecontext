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
def mock_neo4j():
    m = MagicMock()
    m.verify_connectivity.return_value = None
    m.close.return_value = None
    return m


@pytest.fixture
def mock_provider():
    m = MagicMock()

    async def fake_embed(texts):
        return [[0.1] * 768 for _ in texts]

    m.embed = fake_embed
    return m


@pytest.fixture
def client(mock_redis, mock_neo4j, mock_provider):
    noop_consumer = AsyncMock(return_value=None)
    with patch("app.main._get_redis", return_value=mock_redis), \
         patch("app.main._make_provider", return_value=mock_provider), \
         patch("app.neo4j_client.Neo4jClient", return_value=mock_neo4j), \
         patch("app.consumer.run_consumer", noop_consumer):
        from app.main import app
        with TestClient(app) as c:
            yield c
