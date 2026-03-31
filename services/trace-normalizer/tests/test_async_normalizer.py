import pytest
from app.models import TraceEvent, CallNode
from app.normalizer import reconstruct_call_tree


def test_async_call_tree_links_parent_child():
    """Events with task_id and async_call/async_return produce linked sub-trees."""
    events = [
        TraceEvent(type="call", fn="handler", file="a.py", line=1, timestamp_ms=0, task_id=0),
        TraceEvent(type="async_call", fn="fetch_user", file="", line=0, timestamp_ms=5, task_id=1),
        TraceEvent(type="call", fn="fetch_user", file="b.py", line=10, timestamp_ms=6, task_id=1),
        TraceEvent(type="return", fn="fetch_user", file="b.py", line=15, timestamp_ms=20, task_id=1),
        TraceEvent(type="async_return", fn="fetch_user", file="", line=0, timestamp_ms=21, task_id=1),
        TraceEvent(type="return", fn="handler", file="a.py", line=5, timestamp_ms=25, task_id=0),
    ]
    nodes = reconstruct_call_tree(events)
    names = [n.stable_id for n in nodes]
    assert "handler" in names
    assert "fetch_user" in names
    handler_idx = names.index("handler")
    fetch_idx = names.index("fetch_user")
    assert fetch_idx > handler_idx


def test_sync_events_without_task_id_still_work():
    """Old-format events (no task_id) produce correct call tree."""
    events = [
        TraceEvent(type="call", fn="a", file="a.py", line=1, timestamp_ms=0),
        TraceEvent(type="call", fn="b", file="b.py", line=1, timestamp_ms=5),
        TraceEvent(type="return", fn="b", file="b.py", line=2, timestamp_ms=10),
        TraceEvent(type="return", fn="a", file="a.py", line=2, timestamp_ms=15),
    ]
    nodes = reconstruct_call_tree(events)
    assert len(nodes) == 2
    assert nodes[0].stable_id == "b"
    assert nodes[1].stable_id == "a"


def test_mixed_sync_async():
    """Mix of sync and async events."""
    events = [
        TraceEvent(type="call", fn="main", file="a.py", line=1, timestamp_ms=0, task_id=0),
        TraceEvent(type="call", fn="sync_helper", file="a.py", line=5, timestamp_ms=2, task_id=0),
        TraceEvent(type="return", fn="sync_helper", file="a.py", line=6, timestamp_ms=4, task_id=0),
        TraceEvent(type="async_call", fn="async_work", file="", line=0, timestamp_ms=5, task_id=1),
        TraceEvent(type="call", fn="async_work", file="b.py", line=1, timestamp_ms=6, task_id=1),
        TraceEvent(type="return", fn="async_work", file="b.py", line=5, timestamp_ms=10, task_id=1),
        TraceEvent(type="async_return", fn="async_work", file="", line=0, timestamp_ms=11, task_id=1),
        TraceEvent(type="return", fn="main", file="a.py", line=10, timestamp_ms=15, task_id=0),
    ]
    nodes = reconstruct_call_tree(events)
    names = [n.stable_id for n in nodes]
    assert "main" in names
    assert "sync_helper" in names
    assert "async_work" in names
