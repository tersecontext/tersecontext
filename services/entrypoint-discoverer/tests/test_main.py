# tests/test_main.py
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


def _make_client():
    # Patches only needed during import. Safe for /health, /metrics (don't call _get_* at
    # request time) and for 422 validation errors (FastAPI rejects before endpoint body runs).
    with patch("app.main._get_neo4j_driver", return_value=MagicMock()), \
         patch("app.main._get_pg_conn", return_value=MagicMock()), \
         patch("app.main._get_redis", return_value=MagicMock()):
        from app.main import app
        return TestClient(app)


def test_health_returns_200():
    client = _make_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "entrypoint-discoverer"


def test_ready_returns_200_when_redis_ok():
    mock_r = MagicMock()
    mock_r.ping.return_value = True
    with patch("app.main._get_neo4j_driver", return_value=MagicMock()), \
         patch("app.main._get_pg_conn", return_value=MagicMock()), \
         patch("app.main._get_redis", return_value=mock_r):
        from app.main import app, _svc
        _svc._dep_checkers.clear()
        with TestClient(app) as client:
            resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_ready_returns_503_when_redis_down():
    mock_r = MagicMock()
    mock_r.ping.side_effect = Exception("connection refused")
    with patch("app.main._get_neo4j_driver", return_value=MagicMock()), \
         patch("app.main._get_pg_conn", return_value=MagicMock()), \
         patch("app.main._get_redis", return_value=mock_r):
        from app.main import app, _svc
        _svc._dep_checkers.clear()
        with TestClient(app) as client:
            resp = client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert any(v["status"] == "error" for v in body["deps"].values())


def test_metrics_returns_prometheus_text():
    client = _make_client()
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "entrypoint_discoverer" in resp.text


def test_discover_returns_202():
    with patch("app.main._get_neo4j_driver", return_value=MagicMock()), \
         patch("app.main._get_pg_conn", return_value=MagicMock()), \
         patch("app.main._get_redis", return_value=MagicMock()), \
         patch("app.main.run_discover", return_value={"discovered": 5, "queued": 3}):
        from app.main import app
        client = TestClient(app)
        resp = client.post("/discover", json={"repo": "myrepo", "trigger": "schedule"})
    assert resp.status_code == 202
    data = resp.json()
    assert data["discovered"] == 5
    assert data["queued"] == 3


def test_discover_rejects_invalid_trigger():
    client = _make_client()
    resp = client.post("/discover", json={"repo": "myrepo", "trigger": "invalid"})
    assert resp.status_code == 422
