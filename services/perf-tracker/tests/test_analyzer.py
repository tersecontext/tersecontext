import pytest
from datetime import datetime, timezone


def test_detect_queue_buildup():
    from app.analyzer import detect_queue_buildup
    prev = {"stream:raw-traces": 10, "stream:execution-paths": 5}
    curr = {"stream:raw-traces": 25, "stream:execution-paths": 5}
    bottlenecks = detect_queue_buildup(prev, curr, repo="test")
    assert len(bottlenecks) == 1
    assert bottlenecks[0].entity_id == "stream:raw-traces"
    assert bottlenecks[0].category == "pipeline"


def test_detect_queue_buildup_no_issue():
    from app.analyzer import detect_queue_buildup
    prev = {"stream:raw-traces": 10}
    curr = {"stream:raw-traces": 8}
    bottlenecks = detect_queue_buildup(prev, curr, repo="test")
    assert len(bottlenecks) == 0


def test_detect_processing_lag():
    from app.analyzer import detect_processing_lag
    lag_values = [15.0, 12.0, 11.0]
    bottlenecks = detect_processing_lag(lag_values, threshold_s=10.0, repo="test")
    assert len(bottlenecks) == 1
    assert bottlenecks[0].severity == "warning"


def test_detect_processing_lag_ok():
    from app.analyzer import detect_processing_lag
    lag_values = [3.0, 2.0, 5.0]
    bottlenecks = detect_processing_lag(lag_values, threshold_s=10.0, repo="test")
    assert len(bottlenecks) == 0


def test_detect_slow_functions():
    from app.analyzer import detect_slow_functions
    rows = [
        {"entity_id": "sha256:fn_slow", "value": 5000.0, "unit": "ms", "commit_sha": "abc"},
        {"entity_id": "sha256:fn_fast", "value": 10.0, "unit": "ms", "commit_sha": "abc"},
    ]
    bottlenecks = detect_slow_functions(rows, repo="test")
    assert len(bottlenecks) == 2
    assert bottlenecks[0].entity_id == "sha256:fn_slow"
    assert bottlenecks[0].severity == "critical"


def test_detect_deep_chains():
    from app.analyzer import detect_deep_chains
    rows = [
        {"entity_id": "sha256:ep1", "value": 20.0},
        {"entity_id": "sha256:ep2", "value": 5.0},
    ]
    bottlenecks = detect_deep_chains(rows, max_depth=15, repo="test")
    assert len(bottlenecks) == 1
    assert bottlenecks[0].entity_id == "sha256:ep1"


def test_detect_throughput_drop():
    from app.analyzer import detect_throughput_drop
    bottlenecks = detect_throughput_drop(
        current_rate=2.0, rolling_avg=10.0, threshold_pct=50.0,
        stream="stream:raw-traces", repo="test",
    )
    assert len(bottlenecks) == 1
    assert bottlenecks[0].category == "pipeline"
    assert bottlenecks[0].value == 80.0


def test_detect_throughput_drop_ok():
    from app.analyzer import detect_throughput_drop
    bottlenecks = detect_throughput_drop(
        current_rate=8.0, rolling_avg=10.0, threshold_pct=50.0,
        stream="stream:raw-traces", repo="test",
    )
    assert len(bottlenecks) == 0


def test_compute_trend_slope():
    from app.analyzer import compute_trend_slope
    points = [
        {"value": 100.0, "recorded_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        {"value": 110.0, "recorded_at": datetime(2026, 1, 2, tzinfo=timezone.utc)},
        {"value": 120.0, "recorded_at": datetime(2026, 1, 3, tzinfo=timezone.utc)},
    ]
    slope, direction = compute_trend_slope(points)
    assert slope > 0
    assert direction == "increasing"


def test_compute_trend_slope_decreasing():
    from app.analyzer import compute_trend_slope
    points = [
        {"value": 120.0, "recorded_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        {"value": 110.0, "recorded_at": datetime(2026, 1, 2, tzinfo=timezone.utc)},
        {"value": 100.0, "recorded_at": datetime(2026, 1, 3, tzinfo=timezone.utc)},
    ]
    slope, direction = compute_trend_slope(points)
    assert slope < 0
    assert direction == "decreasing"


def test_compute_trend_slope_insufficient_data():
    from app.analyzer import compute_trend_slope
    points = [{"value": 100.0, "recorded_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}]
    slope, direction = compute_trend_slope(points)
    assert slope == 0.0
    assert direction == "stable"
