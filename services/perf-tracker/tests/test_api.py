import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from contextlib import asynccontextmanager


@pytest.fixture
def api_client(mock_store):
    @asynccontextmanager
    async def _noop_lifespan(app):
        yield

    import app.main as main_mod
    original_store = main_mod._store
    original_lifespan = main_mod.app.router.lifespan_context
    main_mod._store = mock_store
    main_mod.app.router.lifespan_context = _noop_lifespan

    with patch("app.main._get_redis", return_value=AsyncMock(ping=AsyncMock())):
        from fastapi.testclient import TestClient
        with TestClient(main_mod.app) as c:
            yield c

    main_mod._store = original_store
    main_mod.app.router.lifespan_context = original_lifespan


def test_health(api_client):
    resp = api_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "perf-tracker"


def test_get_realtime(api_client, mock_store):
    mock_store.get_realtime = AsyncMock(return_value={"throughput": "42.5"})
    resp = api_client.get("/api/v1/realtime/test-repo")
    assert resp.status_code == 200
    assert resp.json()["throughput"] == "42.5"


def test_get_bottlenecks(api_client, mock_store):
    mock_store.get_bottlenecks_json = AsyncMock(return_value=[
        json.dumps({"entity_id": "x", "category": "pipeline", "severity": "warning",
                     "entity_name": "x", "value": 1.0, "unit": "s",
                     "detail": "test", "repo": "test", "detected_at": "2026-01-01T00:00:00Z"}),
    ])
    resp = api_client.get("/api/v1/bottlenecks/test-repo")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1


def test_get_bottlenecks_filter_category(api_client, mock_store):
    mock_store.get_bottlenecks_json = AsyncMock(return_value=[
        json.dumps({"entity_id": "a", "category": "pipeline", "severity": "warning",
                     "entity_name": "a", "value": 1.0, "unit": "s",
                     "detail": "t", "repo": "test", "detected_at": "2026-01-01T00:00:00Z"}),
        json.dumps({"entity_id": "b", "category": "slow_fn", "severity": "warning",
                     "entity_name": "b", "value": 2.0, "unit": "ms",
                     "detail": "t", "repo": "test", "detected_at": "2026-01-01T00:00:00Z"}),
    ])
    resp = api_client.get("/api/v1/bottlenecks/test-repo?category=pipeline")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["category"] == "pipeline"


def test_get_slow_functions(api_client, mock_store):
    mock_store.get_slow_functions = AsyncMock(return_value=[
        {"entity_id": "sha256:fn", "value": 120.0, "unit": "ms", "commit_sha": "abc"},
    ])
    resp = api_client.get("/api/v1/slow-functions/test-repo?limit=5")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_hot_paths(api_client, mock_store):
    mock_store.get_hot_functions = AsyncMock(return_value=[
        ("sha256:fn_auth", 3600.0),
        ("sha256:fn_login", 1200.0),
    ])
    resp = api_client.get("/api/v1/hot-paths/test-repo?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["cumulative_ms"] == 3600.0


def test_get_trends(api_client, mock_store):
    mock_store.get_trends = AsyncMock(return_value=[
        {"value": 100.0, "recorded_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        {"value": 110.0, "recorded_at": datetime(2026, 1, 2, tzinfo=timezone.utc)},
    ])
    resp = api_client.get("/api/v1/trends/test-repo/sha256:fn?metric_name=fn_duration&window=24h")
    assert resp.status_code == 200
    data = resp.json()
    assert "points" in data
    assert "trend" in data


def test_get_regressions(api_client, mock_store):
    mock_store.get_regressions = AsyncMock(return_value=[
        {"entity_id": "sha256:fn", "base_val": 100.0, "head_val": 150.0, "pct_change": 50.0},
    ])
    resp = api_client.get("/api/v1/regressions/test-repo?base_sha=abc&head_sha=def")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_regressions_missing_params(api_client, mock_store):
    resp = api_client.get("/api/v1/regressions/test-repo")
    assert resp.status_code == 422


def test_get_summary(api_client, mock_store):
    mock_store.get_realtime = AsyncMock(return_value={"throughput": "42.5"})
    mock_store.get_slow_functions = AsyncMock(return_value=[])
    mock_store.get_bottlenecks_json = AsyncMock(return_value=[])
    mock_store.get_summary_counts = AsyncMock(return_value={"pipeline": 10, "user_code": 50})
    resp = api_client.get("/api/v1/summary/test-repo")
    assert resp.status_code == 200
    data = resp.json()
    assert "metric_counts" in data


def test_post_analyze(api_client, mock_store):
    mock_store.get_slow_functions = AsyncMock(return_value=[])
    mock_store.get_realtime = AsyncMock(return_value={})
    mock_store.get_bottlenecks_json = AsyncMock(return_value=[])
    mock_store.get_summary_counts = AsyncMock(return_value={})
    resp = api_client.post("/api/v1/analyze/test-repo")
    assert resp.status_code == 200
