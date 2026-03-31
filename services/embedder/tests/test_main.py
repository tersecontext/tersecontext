"""Integration tests for embedder HTTP endpoints (app/main.py)."""
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


def _make_client_patches(redis, neo4j, provider):
    """Return context managers that mock all infra for TestClient."""
    noop_consumer = AsyncMock(return_value=None)
    return (
        patch("app.main._get_redis", return_value=redis),
        patch("app.main._make_provider", return_value=provider),
        patch("app.neo4j_client.Neo4jClient", return_value=neo4j),
        patch("app.consumer.run_consumer", noop_consumer),
    )


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "embedder"
    assert "version" in body


def test_ready_returns_ok_when_all_up(client):
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_ready_returns_503_when_redis_down(mock_neo4j, mock_provider):
    bad_redis = AsyncMock()
    bad_redis.ping.side_effect = Exception("connection refused")
    p1, p2, p3, p4 = _make_client_patches(bad_redis, mock_neo4j, mock_provider)
    with p1, p2, p3, p4:
        from app.main import app
        with TestClient(app) as c:
            resp = c.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert any("redis" in e for e in body["errors"])


def test_ready_returns_503_when_neo4j_down(mock_redis, mock_provider):
    bad_neo4j = MagicMock()
    bad_neo4j.verify_connectivity.side_effect = Exception("neo4j unavailable")
    bad_neo4j.close.return_value = None
    p1, p2, p3, p4 = _make_client_patches(mock_redis, bad_neo4j, mock_provider)
    with p1, p2, p3, p4:
        from app.main import app
        with TestClient(app) as c:
            resp = c.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert any("neo4j" in e for e in body["errors"])


def test_metrics_returns_prometheus_format(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    assert "embedder_messages_processed_total" in text
    assert "embedder_messages_failed_total" in text
    assert "embedder_nodes_embedded_total" in text
    assert "embedder_nodes_skipped_total" in text
    # Verify Prometheus comment format
    assert "# HELP" in text
    assert "# TYPE" in text
