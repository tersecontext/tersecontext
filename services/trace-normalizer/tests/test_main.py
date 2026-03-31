# services/trace-normalizer/tests/test_main.py
import json
from unittest.mock import MagicMock, patch


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["service"] == "trace-normalizer"


def test_ready_returns_ok_when_redis_up(client):
    resp = client.get("/ready")
    assert resp.status_code == 200


def test_ready_returns_503_when_redis_down(mock_redis):
    mock_redis.ping.side_effect = Exception("connection refused")
    with patch("app.main._get_redis", return_value=mock_redis), \
         patch("app.main._get_neo4j_driver", return_value=None):
        from app.main import app
        from fastapi.testclient import TestClient
        with TestClient(app) as c:
            resp = c.get("/ready")
    assert resp.status_code == 503


def test_metrics_returns_prometheus_text(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "trace_normalizer_events_processed_total" in resp.text


def test_process_message_emits_execution_path():
    """Integration: a full raw trace message goes through all modules and emits."""
    from app.main import _process_message

    raw_trace = {
        "entrypoint_stable_id": "sha256:fn_test_login",
        "commit_sha": "a4f91c",
        "repo": "test",
        "duration_ms": 100.0,
        "events": [
            {"type": "call", "fn": "login", "file": "auth.py", "line": 10, "timestamp_ms": 0.0},
            {"type": "call", "fn": "validate_token", "file": "auth.py", "line": 15, "timestamp_ms": 5.0},
            {"type": "return", "fn": "validate_token", "file": "auth.py", "line": 20, "timestamp_ms": 15.0},
            {"type": "return", "fn": "login", "file": "auth.py", "line": 30, "timestamp_ms": 100.0},
        ],
    }
    mock_redis = MagicMock()
    mock_redis.get.return_value = None
    mock_redis.xadd.return_value = b"1-0"

    _process_message(mock_redis, None, json.dumps(raw_trace))

    mock_redis.xadd.assert_called_once()
    stream_name, payload = mock_redis.xadd.call_args[0]
    assert stream_name == "stream:execution-paths"
    ep = json.loads(payload["event"])
    assert ep["entrypoint_stable_id"] == "sha256:fn_test_login"
    assert len(ep["call_sequence"]) >= 1
