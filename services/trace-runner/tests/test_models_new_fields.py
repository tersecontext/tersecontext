from app.models import TraceEvent, RawTrace


def test_trace_event_new_fields_optional():
    evt = TraceEvent(type="call", fn="f", file="a.py", line=1, timestamp_ms=0.0)
    assert evt.task_id is None
    assert evt.args is None
    assert evt.return_val is None


def test_trace_event_with_new_fields():
    evt = TraceEvent(
        type="async_call", fn="f", file="a.py", line=1, timestamp_ms=0.0,
        task_id=1, args='{"x": 1}', return_val='42',
    )
    assert evt.task_id == 1
    assert evt.args == '{"x": 1}'
    assert evt.return_val == "42"


def test_raw_trace_coverage_pct_optional():
    trace = RawTrace(
        entrypoint_stable_id="s", commit_sha="c", repo="r",
        duration_ms=10.0, events=[],
    )
    assert trace.coverage_pct is None


def test_raw_trace_with_coverage_pct():
    trace = RawTrace(
        entrypoint_stable_id="s", commit_sha="c", repo="r",
        duration_ms=10.0, events=[], coverage_pct=87.5,
    )
    assert trace.coverage_pct == 87.5
