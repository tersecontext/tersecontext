# services/instrumenter/tests/test_main.py
import uuid as uuid_lib

from fastapi.testclient import TestClient

from app.main import app

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
