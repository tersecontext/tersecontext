# services/graph-writer/tests/test_main.py
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


# ── Health endpoint ──


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "graph-writer"
    assert "version" in body


# ── Ready endpoint ──


def test_ready_ok(client):
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_ready_503_redis_down(mock_redis, mock_driver):
    mock_redis.ping.side_effect = Exception("connection refused")

    async def _noop(driver):
        return

    with patch("app.main._get_redis", return_value=mock_redis), \
         patch("app.main._make_driver", return_value=mock_driver), \
         patch("app.consumer.run_edge_consumer", side_effect=_noop), \
         patch("app.consumer.run_node_consumer", side_effect=_noop):
        from app.main import app
        with TestClient(app) as c:
            resp = c.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert any("redis" in e for e in body["errors"])


def test_ready_503_neo4j_down(mock_redis):
    async def _noop(driver):
        return

    bad_driver = MagicMock()
    bad_driver.verify_connectivity.side_effect = Exception("neo4j down")

    with patch("app.main._get_redis", return_value=mock_redis), \
         patch("app.main._make_driver", return_value=bad_driver), \
         patch("app.consumer.run_edge_consumer", side_effect=_noop), \
         patch("app.consumer.run_node_consumer", side_effect=_noop):
        from app.main import app
        with TestClient(app) as c:
            resp = c.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert any("neo4j" in e for e in body["errors"])


# ── Metrics endpoint ──


def test_metrics_returns_prometheus_format(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    assert "# HELP" in text
    assert "# TYPE" in text
    assert "graph_writer_messages_processed_total" in text
    assert "graph_writer_edges_written_total" in text
    assert "graph_writer_nodes_written_total" in text
