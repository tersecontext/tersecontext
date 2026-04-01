from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


@asynccontextmanager
async def _noop_lifespan(app):
    yield


def test_health():
    import app.main as main_mod

    original = main_mod.app.router.lifespan_context
    main_mod.app.router.lifespan_context = _noop_lifespan
    try:
        with TestClient(main_mod.app) as client:
            resp = client.get("/health")
    finally:
        main_mod.app.router.lifespan_context = original
    assert resp.status_code == 200
    assert resp.json()["service"] == "trace-runner"


def test_ready_503_when_redis_down():
    """ServiceBase /ready returns 503 when Redis dep checker fails."""
    import app.main as main_mod
    original = main_mod.app.router.lifespan_context

    @asynccontextmanager
    async def _failing_redis_lifespan(app):
        from app.main import _svc
        _svc._dep_checkers.clear()
        async def check_redis() -> str | None:
            return "redis: connection refused"
        _svc.add_dep_checker(check_redis)
        try:
            yield
        finally:
            _svc._dep_checkers.clear()

    main_mod.app.router.lifespan_context = _failing_redis_lifespan
    try:
        with TestClient(main_mod.app) as client:
            resp = client.get("/ready")
    finally:
        main_mod.app.router.lifespan_context = original

    assert resp.status_code == 503
    body = resp.json()
    assert "check_redis" in body["deps"]
    assert body["deps"]["check_redis"]["status"] == "error"
    assert "redis" in body["deps"]["check_redis"]["error"]
