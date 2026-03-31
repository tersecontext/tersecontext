import pytest
from app.models import TraceEvent, RawTrace, CallNode, SideEffect, ExecutionPath


def test_trace_event_roundtrip():
    e = TraceEvent(type="call", fn="authenticate", file="auth/service.py", line=34, timestamp_ms=0.0)
    assert e.type == "call"
    assert e.fn == "authenticate"


def test_raw_trace_roundtrip():
    rt = RawTrace(
        entrypoint_stable_id="sha256:fn_test_login",
        commit_sha="a4f91c",
        repo="test",
        duration_ms=284.0,
        events=[
            TraceEvent(type="call", fn="login", file="auth.py", line=10, timestamp_ms=0.0),
            TraceEvent(type="return", fn="login", file="auth.py", line=20, timestamp_ms=28.0),
        ],
    )
    assert rt.repo == "test"
    assert len(rt.events) == 2


def test_raw_trace_repo_optional():
    rt = RawTrace(
        entrypoint_stable_id="sha256:fn",
        commit_sha="abc",
        duration_ms=10.0,
        events=[],
    )
    assert rt.repo is None


def test_execution_path_roundtrip():
    ep = ExecutionPath(
        entrypoint_stable_id="sha256:fn",
        commit_sha="abc",
        call_sequence=[
            CallNode(stable_id="sha256:fn", hop=0, frequency_ratio=1.0, avg_ms=10.0)
        ],
        side_effects=[],
        dynamic_only_edges=[],
        never_observed_static_edges=[],
        timing_p50_ms=10.0,
        timing_p99_ms=10.0,
    )
    assert ep.timing_p50_ms == 10.0
