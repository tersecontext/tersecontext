# services/repo-watcher/tests/test_main.py
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
import pytest

import app.main as main_module
from app.main import app


@pytest.fixture(autouse=True)
def reset_redis_client():
    """Reset module-level redis singleton between tests to avoid state leakage."""
    original = main_module._redis_client
    yield
    main_module._redis_client = original


def test_health():
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["service"] == "repo-watcher"
    assert resp.json()["version"] == "0.1.0"


def test_ready_ok():
    mock_redis = MagicMock()
    mock_redis.ping.return_value = True
    main_module._redis_client = mock_redis
    with TestClient(app) as client:
        resp = client.get("/ready")
    assert resp.status_code == 200


def test_ready_503_when_redis_fails():
    mock_redis = MagicMock()
    mock_redis.ping.side_effect = Exception("connection refused")
    main_module._redis_client = mock_redis
    with TestClient(app) as client:
        resp = client.get("/ready")
    assert resp.status_code == 503


def test_metrics_returns_text():
    with TestClient(app) as client:
        resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "repo_watcher_events_emitted_total" in resp.text
