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
