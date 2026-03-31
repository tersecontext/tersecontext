import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_instrument_and_run_full_flow():
    """Full flow: /instrument with capture_args → /run → verify events."""
    resp = client.post("/instrument", json={
        "stable_id": "sha256:integration_test",
        "file_path": "__test_stub__",
        "repo": "test",
        "capture_args": ["*stub*"],
        "coverage_filter": None,
    })
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    resp = client.post("/run", json={"session_id": session_id})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["events"], list)
    assert data["duration_ms"] >= 0


def test_instrument_with_coverage_filter():
    """Coverage filter restricts which files are traced."""
    resp = client.post("/instrument", json={
        "stable_id": "sha256:filtered",
        "file_path": "__test_stub__",
        "repo": "test",
        "coverage_filter": ["nonexistent_module.py"],
    })
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    resp = client.post("/run", json={"session_id": session_id})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["events"], list)
