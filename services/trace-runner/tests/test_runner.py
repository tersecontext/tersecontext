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
