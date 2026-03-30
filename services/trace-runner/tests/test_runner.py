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
