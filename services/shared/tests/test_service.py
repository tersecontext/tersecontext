import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shared.service import ServiceBase


def _app(svc: ServiceBase) -> FastAPI:
    """Mount svc.router onto a throwaway FastAPI app for testing."""
    app = FastAPI()
    app.include_router(svc.router)
    return app


def test_health_returns_ok():
    svc = ServiceBase("my-svc", "1.0.0")
    client = TestClient(_app(svc))
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "my-svc", "version": "1.0.0"}


def test_ready_no_checkers_returns_ok():
    svc = ServiceBase("my-svc", "1.0.0")
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ready_failing_checker_returns_503():
    svc = ServiceBase("my-svc", "1.0.0")

    async def bad_dep() -> str | None:
        return "redis: connection refused"

    svc.add_dep_checker(bad_dep)
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    assert resp.status_code == 503
    assert "redis" in resp.json()["errors"][0]


def test_ready_passing_checker_returns_ok():
    svc = ServiceBase("my-svc", "1.0.0")

    async def good_dep() -> str | None:
        return None

    svc.add_dep_checker(good_dep)
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    assert resp.status_code == 200
