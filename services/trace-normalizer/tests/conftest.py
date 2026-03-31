# services/trace-normalizer/tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_redis():
    m = MagicMock()
    m.ping.return_value = True
    m.xreadgroup.return_value = []
    return m


@pytest.fixture
def client(mock_redis):
    with patch("app.main._get_redis", return_value=mock_redis), \
         patch("app.main._get_neo4j_driver", return_value=None):
        from app.main import app
        with TestClient(app) as c:
            yield c
