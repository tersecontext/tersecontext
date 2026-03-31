import json
import pytest
from unittest.mock import MagicMock
from app.models import TraceEvent, CallNode


def _make_events(*spec):
    return [
        TraceEvent(type=t, fn=fn, file="x.py", line=1, timestamp_ms=ts)
        for t, fn, ts in spec
    ]


def test_reconstruct_simple_call_returns_call_node():
    from app.normalizer import reconstruct_call_tree
    events = _make_events(
        ("call", "login", 0),
        ("return", "login", 30),
    )
    nodes = reconstruct_call_tree(events)
    assert len(nodes) == 1
    assert nodes[0].stable_id == "login"
    assert nodes[0].hop == 0
    assert nodes[0].avg_ms == 30.0


def test_reconstruct_nested_calls_assigns_hop_depth():
    from app.normalizer import reconstruct_call_tree
    events = _make_events(
        ("call", "outer", 0),
        ("call", "inner", 5),
        ("return", "inner", 15),
        ("return", "outer", 20),
    )
    nodes = reconstruct_call_tree(events)
    outer = next(n for n in nodes if n.stable_id == "outer")
    inner = next(n for n in nodes if n.stable_id == "inner")
    assert outer.hop == 0
    assert inner.hop == 1
    assert inner.avg_ms == 10.0


def test_reconstruct_frequency_ratio_defaults_to_1():
    from app.normalizer import reconstruct_call_tree
    events = _make_events(("call", "fn", 0), ("return", "fn", 5))
    nodes = reconstruct_call_tree(events)
    assert nodes[0].frequency_ratio == 1.0


def test_aggregate_updates_redis_and_returns_nodes():
    from app.normalizer import aggregate_frequencies
    mock_redis = MagicMock()
    # Simulate 4 previous runs stored in Redis
    agg_data = json.dumps({"login": {"count": 4, "total_ms": 120.0, "runs": 4}})
    mock_redis.get.return_value = agg_data.encode()

    nodes_in = [CallNode(stable_id="login", hop=0, frequency_ratio=1.0, avg_ms=30.0)]
    result = aggregate_frequencies(mock_redis, "test", "sha256:ep", nodes_in, total_runs=5)

    # After 5 runs, frequencies should be recalculated
    assert result[0].stable_id == "login"
    assert result[0].frequency_ratio == 1.0   # seen in all 5 runs
    assert result[0].avg_ms == pytest.approx((120.0 + 30.0) / 5, rel=0.01)
    mock_redis.set.assert_called_once()


def test_compute_percentiles_single_sample():
    from app.normalizer import compute_percentiles
    assert compute_percentiles([42.0]) == (42.0, 42.0)


def test_compute_percentiles_multiple():
    from app.normalizer import compute_percentiles
    samples = list(range(1, 101))  # 1..100
    p50, p99 = compute_percentiles(samples)
    assert p50 == pytest.approx(50.5, rel=0.1)
    assert p99 == pytest.approx(99.0, rel=0.1)
