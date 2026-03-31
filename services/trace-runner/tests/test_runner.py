import pytest

# ── Models ────────────────────────────────────────────────────────────────────


def test_entrypoint_job_parses_from_redis_json():
    import json
    from app.models import EntrypointJob
    raw = '{"stable_id":"sha256:fn_test_login","name":"test_login","file_path":"tests/test_auth.py","priority":2,"repo":"test"}'
    job = EntrypointJob.model_validate_json(raw)
    assert job.stable_id == "sha256:fn_test_login"
    assert job.priority == 2
    assert job.repo == "test"


def test_trace_event_required_fields():
    from app.models import TraceEvent
    evt = TraceEvent(type="call", fn="authenticate", file="auth/service.py", line=34, timestamp_ms=0.0)
    assert evt.type == "call"
    assert evt.exc_type is None


def test_trace_event_exception_type_optional():
    from app.models import TraceEvent
    evt = TraceEvent(type="exception", fn="authenticate", file="auth/service.py", line=40, timestamp_ms=5.0, exc_type="ValueError")
    assert evt.exc_type == "ValueError"


def test_raw_trace_serialises_to_contract_shape():
    from app.models import RawTrace, TraceEvent
    trace = RawTrace(
        entrypoint_stable_id="sha256:fn_test_login",
        commit_sha="abc123",
        repo="test",
        duration_ms=100.0,
        events=[
            TraceEvent(type="call", fn="authenticate", file="auth/service.py", line=34, timestamp_ms=0.0),
            TraceEvent(type="return", fn="authenticate", file="auth/service.py", line=52, timestamp_ms=28.0),
        ],
    )
    data = trace.model_dump()
    assert data["entrypoint_stable_id"] == "sha256:fn_test_login"
    assert len(data["events"]) == 2
    assert data["events"][0]["type"] == "call"


# ── Cache ─────────────────────────────────────────────────────────────────────

def test_cache_key_format():
    from app.cache import cache_key
    key = cache_key("abc123", "sha256:fn_test_login")
    assert key == "trace_cache:abc123:sha256:fn_test_login"


async def test_is_cached_returns_false_when_missing():
    from unittest.mock import AsyncMock
    from app.cache import is_cached
    mock_r = AsyncMock()
    mock_r.exists.return_value = 0
    result = await is_cached(mock_r, "abc123", "sha256:fn_test")
    assert result is False
    mock_r.exists.assert_called_once_with("trace_cache:abc123:sha256:fn_test")


async def test_is_cached_returns_true_when_present():
    from unittest.mock import AsyncMock
    from app.cache import is_cached
    mock_r = AsyncMock()
    mock_r.exists.return_value = 1
    result = await is_cached(mock_r, "abc123", "sha256:fn_test")
    assert result is True


async def test_mark_cached_sets_key_with_ttl():
    from unittest.mock import AsyncMock
    from app.cache import mark_cached, CACHE_TTL_SECONDS
    mock_r = AsyncMock()
    await mark_cached(mock_r, "abc123", "sha256:fn_test")
    mock_r.set.assert_called_once_with(
        "trace_cache:abc123:sha256:fn_test", "1", ex=CACHE_TTL_SECONDS
    )


def test_cache_ttl_is_24_hours():
    from app.cache import CACHE_TTL_SECONDS
    assert CACHE_TTL_SECONDS == 86400


# ── Instrumenter Client ───────────────────────────────────────────────────────

async def test_instrument_returns_session_id():
    from unittest.mock import AsyncMock, MagicMock
    from app.instrumenter_client import InstrumenterClient

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"session_id": "sess-uuid", "status": "ready"}

    client = InstrumenterClient(base_url="http://localhost:8093")
    client._client.post = AsyncMock(return_value=mock_response)
    session_id = await client.instrument(
        stable_id="sha256:fn_test_login",
        file_path="tests/test_auth.py",
        repo="test",
    )
    await client.aclose()
    assert session_id == "sess-uuid"


async def test_run_returns_events_and_duration():
    from unittest.mock import AsyncMock, MagicMock
    from app.instrumenter_client import InstrumenterClient
    from app.models import TraceEvent

    events_data = [
        {"type": "call", "fn": "authenticate", "file": "auth/service.py", "line": 34, "timestamp_ms": 0.0},
        {"type": "return", "fn": "authenticate", "file": "auth/service.py", "line": 52, "timestamp_ms": 28.0},
    ]
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"events": events_data, "duration_ms": 28.0}

    client = InstrumenterClient(base_url="http://localhost:8093")
    client._client.post = AsyncMock(return_value=mock_response)
    events, duration_ms = await client.run(session_id="sess-uuid")
    await client.aclose()

    assert len(events) == 2
    assert isinstance(events[0], TraceEvent)
    assert duration_ms == 28.0


async def test_run_raises_on_http_error():
    from unittest.mock import AsyncMock, MagicMock
    import httpx
    from app.instrumenter_client import InstrumenterClient

    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=MagicMock()
    )

    client = InstrumenterClient(base_url="http://localhost:8093")
    client._client.post = AsyncMock(return_value=mock_response)
    with pytest.raises(httpx.HTTPStatusError):
        await client.run(session_id="sess-uuid")
    await client.aclose()


async def test_instrument_raises_on_http_error():
    import httpx
    from unittest.mock import AsyncMock, MagicMock
    from app.instrumenter_client import InstrumenterClient

    client = InstrumenterClient(base_url="http://localhost:8093")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "404", request=MagicMock(), response=MagicMock()
    )
    client._client.post = AsyncMock(return_value=mock_resp)

    with pytest.raises(httpx.HTTPStatusError):
        await client.instrument(stable_id="sha256:fn", file_path="f.py", repo="test")
    await client.aclose()


# ── Runner ────────────────────────────────────────────────────────────────────

async def test_process_job_skips_when_cached():
    from unittest.mock import AsyncMock, MagicMock
    from app.models import EntrypointJob
    from app.runner import process_job

    job = EntrypointJob(
        stable_id="sha256:fn_test", name="test_fn",
        file_path="tests/test.py", priority=2, repo="test",
    )
    mock_r = AsyncMock()
    mock_r.exists.return_value = 1  # cache hit

    mock_client = MagicMock()
    emitted = []

    async def fake_emit(r, trace):
        emitted.append(trace)

    result = await process_job(
        job=job,
        commit_sha="abc123",
        r=mock_r,
        instrumenter=mock_client,
        emit_fn=fake_emit,
    )

    assert result == "cached"
    mock_client.instrument.assert_not_called()
    assert emitted == []


async def test_process_job_calls_instrumenter_and_emits():
    from unittest.mock import AsyncMock, MagicMock
    from app.models import EntrypointJob, TraceEvent
    from app.runner import process_job

    job = EntrypointJob(
        stable_id="sha256:fn_test", name="test_fn",
        file_path="tests/test.py", priority=2, repo="test",
    )
    mock_r = AsyncMock()
    mock_r.exists.return_value = 0  # cache miss

    events = [
        TraceEvent(type="call", fn="test_fn", file="tests/test.py", line=1, timestamp_ms=0.0),
        TraceEvent(type="return", fn="test_fn", file="tests/test.py", line=5, timestamp_ms=10.0),
    ]

    mock_client = AsyncMock()
    mock_client.instrument.return_value = "sess-uuid"
    mock_client.run.return_value = (events, 10.0)

    emitted = []

    async def fake_emit(r, trace):
        emitted.append(trace)

    result = await process_job(
        job=job,
        commit_sha="abc123",
        r=mock_r,
        instrumenter=mock_client,
        emit_fn=fake_emit,
    )

    assert result == "ok"
    mock_client.instrument.assert_called_once_with(
        stable_id="sha256:fn_test", file_path="tests/test.py", repo="test"
    )
    mock_client.run.assert_called_once_with(session_id="sess-uuid")
    assert len(emitted) == 1
    emitted_trace = emitted[0]
    assert emitted_trace.entrypoint_stable_id == "sha256:fn_test"
    assert emitted_trace.commit_sha == "abc123"
    assert emitted_trace.duration_ms == 10.0
    assert len(emitted_trace.events) == 2
    # cache was marked
    mock_r.set.assert_called_once()


async def test_process_job_returns_error_on_instrumenter_failure():
    from unittest.mock import AsyncMock, MagicMock
    from app.models import EntrypointJob
    from app.runner import process_job

    job = EntrypointJob(
        stable_id="sha256:fn_test", name="test_fn",
        file_path="tests/test.py", priority=2, repo="test",
    )
    mock_r = AsyncMock()
    mock_r.exists.return_value = 0

    mock_client = AsyncMock()
    mock_client.instrument.side_effect = Exception("instrumenter unavailable")

    emitted = []

    async def fake_emit(r, trace):
        emitted.append(trace)

    result = await process_job(
        job=job,
        commit_sha="abc123",
        r=mock_r,
        instrumenter=mock_client,
        emit_fn=fake_emit,
    )

    assert result == "error"
    assert emitted == []
    # cache was NOT marked on failure
    mock_r.set.assert_not_called()


# ── App endpoints ─────────────────────────────────────────────────────────────

import app.main as main_module
from app.main import app as fastapi_app


@pytest.fixture(autouse=True)
def reset_redis_client():
    """Reset module-level redis singleton between tests to avoid state leakage."""
    original = main_module._redis_client
    yield
    main_module._redis_client = original


def test_health_returns_200():
    from fastapi.testclient import TestClient
    from unittest.mock import patch, AsyncMock
    with patch("app.main._start_worker", new=AsyncMock(return_value=None)):
        with TestClient(fastapi_app) as client:
            resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "trace-runner"


def test_ready_returns_503_when_redis_down():
    from fastapi.testclient import TestClient
    from unittest.mock import patch, AsyncMock
    with patch("app.main._start_worker", new=AsyncMock(return_value=None)):
        with patch("app.main._get_redis") as mock_get_redis:
            mock_r = AsyncMock()
            mock_r.ping.side_effect = Exception("connection refused")
            mock_get_redis.return_value = mock_r
            with TestClient(fastapi_app) as client:
                resp = client.get("/ready")
    assert resp.status_code == 503


def test_metrics_returns_prometheus_text():
    from fastapi.testclient import TestClient
    from unittest.mock import patch, AsyncMock
    with patch("app.main._start_worker", new=AsyncMock(return_value=None)):
        with TestClient(fastapi_app) as client:
            resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "trace_runner_jobs_processed_total" in resp.text
