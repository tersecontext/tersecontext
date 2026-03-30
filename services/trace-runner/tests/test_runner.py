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
