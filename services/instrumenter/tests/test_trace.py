import os
import sysconfig
import tempfile

import pytest
from app.trace import create_trace_func, TraceSession


def _make_session():
    td = tempfile.mkdtemp()
    return TraceSession(
        session_id="test-001",
        file_path="test_module.py",
        repo="test",
        stable_id="sha256:fn_test",
        tempdir=td,
    )


def test_stdlib_frames_are_excluded():
    """Frames from stdlib should return None (subtree pruning), not trace_func."""
    session = _make_session()
    trace_func = create_trace_func(session)

    stdlib_prefix = sysconfig.get_path("stdlib")
    stdlib_file = os.path.join(stdlib_prefix, "importlib", "__init__.py")

    class FakeFrame:
        class f_code:
            co_filename = stdlib_file
            co_name = "something"
            co_firstlineno = 1
        f_lineno = 1
        f_locals = {}

    result = trace_func(FakeFrame(), "call", None)
    assert result is None, f"Expected None for stdlib frame, got {result!r}"
    assert len(session.events) == 0, "No events should be emitted for stdlib frames"


def test_user_code_frames_are_traced():
    """Frames from user code should return trace_func (continue tracing)."""
    session = _make_session()
    trace_func = create_trace_func(session)

    class FakeFrame:
        class f_code:
            co_filename = "/repos/myproject/app/auth.py"
            co_name = "authenticate"
            co_firstlineno = 10
        f_lineno = 10
        f_locals = {}

    result = trace_func(FakeFrame(), "call", None)
    assert result is trace_func
    assert len(session.events) == 1


def test_trace_func_captures_call_return():
    """Basic sync call chain produces call+return events in order."""
    session = TraceSession(
        session_id="test", file_path="test.py", repo="test", stable_id="test",
    )
    trace_func = create_trace_func(session)

    def target():
        return 42

    import sys
    sys.settrace(trace_func)
    target()
    sys.settrace(None)

    call_events = [e for e in session.events if e.fn == "target" and e.type == "call"]
    return_events = [e for e in session.events if e.fn == "target" and e.type == "return"]
    assert len(call_events) >= 1
    assert len(return_events) >= 1
    assert call_events[0].timestamp_ms <= return_events[0].timestamp_ms


def test_trace_func_coverage_filter_skips():
    """Functions in files not in coverage_filter produce no events."""
    session = TraceSession(
        session_id="test", file_path="test.py", repo="test", stable_id="test",
        coverage_filter={"nonexistent_file.py"},
    )
    trace_func = create_trace_func(session)

    def target():
        return 1

    import sys
    sys.settrace(trace_func)
    target()
    sys.settrace(None)

    target_events = [e for e in session.events if e.fn == "target"]
    assert len(target_events) == 0


def test_trace_func_arg_capture():
    """Functions matching capture_args patterns get args/return_val."""
    session = TraceSession(
        session_id="test", file_path="test.py", repo="test", stable_id="test",
        capture_args=["my_handler"],
    )
    trace_func = create_trace_func(session)

    def my_handler(x, y):
        return x + y

    import sys
    sys.settrace(trace_func)
    my_handler(3, 4)
    sys.settrace(None)

    calls = [e for e in session.events if e.fn == "my_handler" and e.type == "call"]
    returns = [e for e in session.events if e.fn == "my_handler" and e.type == "return"]
    assert len(calls) >= 1
    assert calls[0].args is not None
    assert "3" in calls[0].args
    assert len(returns) >= 1
    assert returns[0].return_val is not None
    assert "7" in returns[0].return_val


def test_trace_func_no_arg_capture_when_not_matching():
    """Functions NOT matching capture_args don't get args."""
    session = TraceSession(
        session_id="test", file_path="test.py", repo="test", stable_id="test",
        capture_args=["*handler*"],
    )
    trace_func = create_trace_func(session)

    def other_func():
        return 1

    import sys
    sys.settrace(trace_func)
    other_func()
    sys.settrace(None)

    calls = [e for e in session.events if e.fn == "other_func" and e.type == "call"]
    assert all(e.args is None for e in calls)
