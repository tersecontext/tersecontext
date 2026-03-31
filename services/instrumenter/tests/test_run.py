import pytest
from fastapi.testclient import TestClient
from app.main import app, _sessions, _session_created_at

client = TestClient(app)


@pytest.fixture(autouse=True)
def _cleanup_sessions():
    """Ensure session state is clean before and after each test."""
    _sessions.clear()
    _session_created_at.clear()
    yield
    _sessions.clear()
    _session_created_at.clear()


def test_run_unknown_session_returns_404():
    resp = client.post("/run", json={"session_id": "nonexistent"})
    assert resp.status_code == 404


def test_instrument_then_run_returns_events():
    resp = client.post("/instrument", json={
        "stable_id": "sha256:test",
        "file_path": "__test_stub__",
        "repo": "test",
    })
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    resp = client.post("/run", json={"session_id": session_id})
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert "duration_ms" in data
    assert isinstance(data["events"], list)
    assert data["duration_ms"] >= 0


def test_run_same_session_twice_returns_404():
    resp = client.post("/instrument", json={
        "stable_id": "sha256:test",
        "file_path": "__test_stub__",
        "repo": "test",
    })
    session_id = resp.json()["session_id"]
    client.post("/run", json={"session_id": session_id})

    resp = client.post("/run", json={"session_id": session_id})
    assert resp.status_code == 404


def test_instrument_with_capture_args():
    resp = client.post("/instrument", json={
        "stable_id": "sha256:test",
        "file_path": "__test_stub__",
        "repo": "test",
        "capture_args": ["*handler*"],
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"
    # Clean up the session
    client.post("/run", json={"session_id": resp.json()["session_id"]})


def test_instrument_max_sessions_returns_503(monkeypatch):
    from app import main
    monkeypatch.setattr(main, "MAX_SESSIONS", 1)
    r1 = client.post("/instrument", json={
        "stable_id": "s1", "file_path": "__test_stub__", "repo": "r",
    })
    assert r1.status_code == 200
    r2 = client.post("/instrument", json={
        "stable_id": "s2", "file_path": "__test_stub__", "repo": "r",
    })
    assert r2.status_code == 503
    # Clean up
    client.post("/run", json={"session_id": r1.json()["session_id"]})
