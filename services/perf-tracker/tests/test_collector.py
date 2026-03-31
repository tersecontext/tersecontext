import pytest
import json


def test_extract_function_durations_from_raw_trace():
    from app.collector import extract_metrics_from_raw_trace
    raw = {
        "entrypoint_stable_id": "sha256:fn_test_login",
        "commit_sha": "a4f91c",
        "repo": "test",
        "duration_ms": 284.0,
        "events": [
            {"type": "call", "fn": "authenticate", "file": "auth.py", "line": 34, "timestamp_ms": 0.0},
            {"type": "return", "fn": "authenticate", "file": "auth.py", "line": 52, "timestamp_ms": 28.0},
            {"type": "call", "fn": "get_user", "file": "auth.py", "line": 60, "timestamp_ms": 30.0},
            {"type": "return", "fn": "get_user", "file": "auth.py", "line": 75, "timestamp_ms": 55.0},
        ],
    }
    metrics = extract_metrics_from_raw_trace(raw)
    fn_durations = [m for m in metrics if m.metric_name == "fn_duration"]
    assert len(fn_durations) == 2
    auth_m = next(m for m in fn_durations if "authenticate" in m.entity_id)
    assert auth_m.value == 28.0
    assert auth_m.unit == "ms"
    assert auth_m.commit_sha == "a4f91c"

    ep_duration = [m for m in metrics if m.metric_name == "entrypoint_duration"]
    assert len(ep_duration) == 1
    assert ep_duration[0].value == 284.0


def test_extract_function_durations_unmatched_return():
    from app.collector import extract_metrics_from_raw_trace
    raw = {
        "entrypoint_stable_id": "sha256:x",
        "commit_sha": "abc",
        "repo": "test",
        "duration_ms": 10.0,
        "events": [
            {"type": "return", "fn": "orphan", "file": "x.py", "line": 1, "timestamp_ms": 5.0},
        ],
    }
    metrics = extract_metrics_from_raw_trace(raw)
    fn_durations = [m for m in metrics if m.metric_name == "fn_duration"]
    assert len(fn_durations) == 0


def test_extract_metrics_from_execution_path():
    from app.collector import extract_metrics_from_execution_path
    path = {
        "entrypoint_stable_id": "sha256:fn_test_login",
        "commit_sha": "a4f91c",
        "repo": "test",
        "call_sequence": [
            {"stable_id": "sha256:auth", "name": "authenticate", "qualified_name": "auth.authenticate", "hop": 0, "frequency_ratio": 1.0, "avg_ms": 28.0},
            {"stable_id": "sha256:get_user", "name": "get_user", "qualified_name": "auth.get_user", "hop": 1, "frequency_ratio": 0.9, "avg_ms": 25.0},
        ],
        "side_effects": [
            {"type": "db_read", "detail": "SELECT * FROM users", "hop_depth": 1},
        ],
        "dynamic_only_edges": [],
        "never_observed_static_edges": [],
        "timing_p50_ms": 50.0,
        "timing_p99_ms": 200.0,
    }
    metrics = extract_metrics_from_execution_path(path)
    call_depth = [m for m in metrics if m.metric_name == "call_depth"]
    assert len(call_depth) == 1
    assert call_depth[0].value == 2

    side_effect_count = [m for m in metrics if m.metric_name == "side_effect_count"]
    assert len(side_effect_count) == 1
    assert side_effect_count[0].value == 1

    p50 = [m for m in metrics if m.metric_name == "timing_p50"]
    assert len(p50) == 1
    assert p50[0].value == 50.0


def test_extract_call_frequency():
    from app.collector import extract_metrics_from_raw_trace
    raw = {
        "entrypoint_stable_id": "sha256:ep",
        "commit_sha": "abc",
        "repo": "test",
        "duration_ms": 100.0,
        "events": [
            {"type": "call", "fn": "foo", "file": "a.py", "line": 1, "timestamp_ms": 0.0},
            {"type": "return", "fn": "foo", "file": "a.py", "line": 2, "timestamp_ms": 10.0},
            {"type": "call", "fn": "foo", "file": "a.py", "line": 1, "timestamp_ms": 20.0},
            {"type": "return", "fn": "foo", "file": "a.py", "line": 2, "timestamp_ms": 30.0},
        ],
    }
    metrics = extract_metrics_from_raw_trace(raw)
    freq = [m for m in metrics if m.metric_name == "call_frequency"]
    foo_freq = next(m for m in freq if "foo" in m.entity_id)
    assert foo_freq.value == 2
