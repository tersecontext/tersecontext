# services/instrumenter/tests/test_main.py
import uuid as uuid_lib

from fastapi.testclient import TestClient

from app.main import app, MAX_SESSIONS

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "instrumenter"
    assert data["version"] == "0.1.0"


def test_ready():
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_metrics():
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "instrumenter_sessions_created_total" in resp.text


def test_instrument_happy_path():
    resp = client.post("/instrument", json={
        "stable_id": "sha256:fn_test_login",
        "file_path": "tests/test_auth.py",
        "repo": "test",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    uuid_lib.UUID(data["session_id"])  # raises ValueError if not a valid UUID
    assert data["session_id"] in data["output_key"]
    assert data["output_key"] == f"trace_events:{data['session_id']}"
    assert isinstance(data["patches"], list)
    assert len(data["patches"]) > 0
    assert data["timeout_ms"] > 0
    assert data["tempdir"].endswith(data["session_id"])


def test_instrument_unique_session_ids():
    payload = {"stable_id": "sha256:fn", "file_path": "f.py", "repo": "r"}
    r1 = client.post("/instrument", json=payload).json()
    r2 = client.post("/instrument", json=payload).json()
    assert r1["session_id"] != r2["session_id"]


def test_instrument_missing_field_returns_422():
    resp = client.post("/instrument", json={"stable_id": "x", "file_path": "y"})
    assert resp.status_code == 422


def test_instrument_empty_stable_id_returns_400():
    resp = client.post("/instrument", json={
        "stable_id": "",
        "file_path": "tests/test_auth.py",
        "repo": "test",
    })
    assert resp.status_code == 400


def test_instrument_empty_file_path_returns_400():
    resp = client.post("/instrument", json={
        "stable_id": "sha256:fn",
        "file_path": "",
        "repo": "test",
    })
    assert resp.status_code == 400


def test_instrument_empty_repo_returns_400():
    resp = client.post("/instrument", json={
        "stable_id": "sha256:fn",
        "file_path": "tests/test_auth.py",
        "repo": "",
    })
    assert resp.status_code == 400


def test_instrument_all_known_patch_targets_present():
    resp = client.post("/instrument", json={
        "stable_id": "sha256:fn",
        "file_path": "f.py",
        "repo": "r",
    })
    targets = {p["target"] for p in resp.json()["patches"]}
    expected = {
        "sqlalchemy.orm.Session.execute",
        "sqlalchemy.engine.Engine.connect",
        "psycopg2.connect",
        "httpx.Client.request",
        "httpx.AsyncClient.request",
        "requests.request",
        "builtins.open",
    }
    assert expected.issubset(targets)


def test_instrument_returns_limit_in_503_when_sessions_full(monkeypatch):
    """Error message must include the session limit so operators know the cap."""
    import app.main as main_mod

    # Fill sessions to the limit with fake entries
    fake_sessions = {f"fake-{i}": object() for i in range(MAX_SESSIONS)}
    fake_times = {f"fake-{i}": 0.0 for i in range(MAX_SESSIONS)}

    monkeypatch.setattr("app.main._sessions", fake_sessions)
    monkeypatch.setattr("app.main._session_created_at", fake_times)

    resp = client.post("/instrument", json={
        "stable_id": "sha256:fn_test", "file_path": "tests/test.py", "repo": "test"
    })

    assert resp.status_code == 503
    error_msg = resp.json()["error"]
    assert str(MAX_SESSIONS) in error_msg
    assert "limit" in error_msg.lower()
