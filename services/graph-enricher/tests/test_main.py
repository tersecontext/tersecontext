# services/graph-enricher/tests/test_main.py
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


def _noop_consumer():
    async def _noop(driver):
        return
    return _noop


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "graph-enricher"
    assert "version" in body


def test_ready_ok(client):
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_ready_503_redis_down(mock_redis, mock_driver):
    mock_redis.ping.side_effect = Exception("connection refused")
    with patch("app.main._make_driver", return_value=mock_driver), \
         patch("app.main._svc.get_redis", return_value=mock_redis), \
         patch("app.consumer.run_consumer", new=_noop_consumer()):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as c:
            resp = c.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert any("redis" in k or "redis" in v.get("error", "") for k, v in body["deps"].items())


def test_ready_503_neo4j_down(mock_redis):
    bad_driver = MagicMock()
    bad_driver.verify_connectivity.side_effect = Exception("neo4j unavailable")
    with patch("app.main._make_driver", return_value=bad_driver), \
         patch("app.main._svc.get_redis", return_value=mock_redis), \
         patch("app.consumer.run_consumer", new=_noop_consumer()):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as c:
            resp = c.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert any("neo4j" in k or "neo4j" in v.get("error", "") for k, v in body["deps"].items())


def test_metrics_returns_prometheus_format(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    assert "graph_enricher_messages_processed_total" in text
    assert "graph_enricher_nodes_enriched_total" in text
    assert "graph_enricher_dynamic_edges_total" in text
    assert "graph_enricher_confirmed_edges_total" in text
    # Verify Prometheus format: has TYPE and HELP lines
    assert "# TYPE" in text
    assert "# HELP" in text
