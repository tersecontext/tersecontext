# services/vector-writer/tests/test_main.py
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "vector-writer"
    assert "version" in body


def test_ready_ok(client):
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_ready_503_redis_down(mock_redis, mock_writer):
    mock_redis.ping.side_effect = Exception("connection refused")

    async def _noop(writer):
        return

    with patch("app.writer.QdrantWriter", return_value=mock_writer), \
         patch("app.consumer.run_embedded_consumer", _noop), \
         patch("app.consumer.run_delete_consumer", _noop), \
         patch("app.main._get_redis", return_value=mock_redis):
        from app.main import app
        import app.main as main_mod
        main_mod._writer = mock_writer
        with TestClient(app) as c:
            resp = c.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert any("redis" in e for e in body["errors"])


def test_ready_503_qdrant_down(mock_redis, mock_writer):
    mock_writer.get_collections.side_effect = Exception("qdrant unreachable")

    async def _noop(writer):
        return

    with patch("app.writer.QdrantWriter", return_value=mock_writer), \
         patch("app.consumer.run_embedded_consumer", _noop), \
         patch("app.consumer.run_delete_consumer", _noop), \
         patch("app.main._get_redis", return_value=mock_redis):
        from app.main import app
        import app.main as main_mod
        main_mod._writer = mock_writer
        with TestClient(app) as c:
            resp = c.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert any("qdrant" in e for e in body["errors"])


def test_metrics_returns_prometheus_format(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    text = resp.text
    assert "vector_writer_messages_processed_total" in text
    assert "vector_writer_messages_failed_total" in text
    assert "vector_writer_points_upserted_total" in text
    assert "vector_writer_points_deleted_total" in text
    # Verify Prometheus format: # HELP and # TYPE lines present
    assert "# HELP" in text
    assert "# TYPE" in text
