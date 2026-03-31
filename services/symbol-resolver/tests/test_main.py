# services/symbol-resolver/tests/test_main.py
from unittest.mock import AsyncMock, MagicMock, patch


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["service"] == "symbol-resolver"


def test_ready_ok(client):
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_ready_503_redis_down(mock_driver):
    mock_redis = AsyncMock()
    mock_redis.ping.side_effect = Exception("connection refused")
    with patch("app.main._get_redis", return_value=mock_redis), \
         patch("app.main.make_driver", return_value=mock_driver), \
         patch("app.main.run_consumer", new_callable=AsyncMock) as mock_consumer:
        import asyncio

        async def _hang(*args, **kwargs):
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
        mock_consumer.side_effect = _hang

        import app.main as main_mod
        from app.main import app
        from fastapi.testclient import TestClient
        with TestClient(app) as c:
            resp = c.get("/ready")
        main_mod._redis_client = None
        main_mod._driver = None
    assert resp.status_code == 503
    assert "redis" in resp.json()["errors"][0]


def test_ready_503_neo4j_down():
    mock_redis = AsyncMock()
    mock_redis.ping.return_value = True
    bad_driver = MagicMock()
    bad_driver.verify_connectivity.side_effect = Exception("neo4j unreachable")
    with patch("app.main._get_redis", return_value=mock_redis), \
         patch("app.main.make_driver", return_value=bad_driver), \
         patch("app.main.run_consumer", new_callable=AsyncMock) as mock_consumer:
        import asyncio

        async def _hang(*args, **kwargs):
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
        mock_consumer.side_effect = _hang

        import app.main as main_mod
        from app.main import app
        from fastapi.testclient import TestClient
        with TestClient(app) as c:
            resp = c.get("/ready")
        main_mod._redis_client = None
        main_mod._driver = None
    assert resp.status_code == 503
    assert "neo4j" in resp.json()["errors"][0]


def test_metrics_returns_prometheus_format(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert "symbol_resolver_messages_processed_total" in resp.text
    assert "# HELP" in resp.text
    assert "# TYPE" in resp.text
