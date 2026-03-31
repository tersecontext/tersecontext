import pytest
from datetime import datetime, timezone


def test_trace_event_from_dict():
    from app.models import TraceEvent
    e = TraceEvent(type="call", fn="authenticate", file="auth.py", line=34, timestamp_ms=0.0)
    assert e.fn == "authenticate"
    assert e.type == "call"


def test_raw_trace_from_dict():
    from app.models import RawTrace
    rt = RawTrace(
        entrypoint_stable_id="sha256:abc",
        commit_sha="a4f91c",
        repo="test",
        duration_ms=284.0,
        events=[
            {"type": "call", "fn": "auth", "file": "a.py", "line": 1, "timestamp_ms": 0.0},
            {"type": "return", "fn": "auth", "file": "a.py", "line": 2, "timestamp_ms": 28.0},
        ],
    )
    assert rt.repo == "test"
    assert len(rt.events) == 2


def test_call_sequence_item():
    from app.models import CallSequenceItem
    item = CallSequenceItem(
        stable_id="sha256:fn", name="auth", qualified_name="mod.auth",
        hop=1, frequency_ratio=0.95, avg_ms=12.5,
    )
    assert item.hop == 1


def test_execution_path_from_dict():
    from app.models import ExecutionPath
    ep = ExecutionPath(
        entrypoint_stable_id="sha256:abc",
        commit_sha="a4f91c",
        repo="test",
        call_sequence=[],
        side_effects=[],
        dynamic_only_edges=[],
        never_observed_static_edges=[],
        timing_p50_ms=10.0,
        timing_p99_ms=50.0,
    )
    assert ep.repo == "test"


def test_perf_metric_model():
    from app.models import PerfMetric
    m = PerfMetric(
        metric_type="pipeline", metric_name="stream_throughput",
        entity_id="stream:raw-traces", repo="test",
        value=42.5, unit="msg/s",
    )
    assert m.metric_type == "pipeline"


def test_bottleneck_model():
    from app.models import Bottleneck
    b = Bottleneck(
        entity_id="stream:raw-traces", entity_name="raw-traces stream",
        category="pipeline", severity="warning",
        value=15.2, unit="s", detail="Processing lag exceeds threshold",
        repo="test", detected_at=datetime.now(timezone.utc),
    )
    assert b.severity == "warning"


def test_execution_path_ignores_extra_fields():
    from app.models import ExecutionPath
    ep = ExecutionPath(
        entrypoint_stable_id="sha256:abc",
        commit_sha="a4f91c",
        repo="test",
        call_sequence=[], side_effects=[],
        dynamic_only_edges=[], never_observed_static_edges=[],
        timing_p50_ms=10.0, timing_p99_ms=50.0,
        some_future_field="ignored",
    )
    assert not hasattr(ep, "some_future_field")
