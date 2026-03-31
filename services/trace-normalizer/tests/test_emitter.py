import json
from unittest.mock import MagicMock
from app.models import ExecutionPath, CallNode


def _make_ep(**kwargs):
    defaults = dict(
        entrypoint_stable_id="sha256:fn_test_login",
        commit_sha="a4f91c",
        call_sequence=[CallNode(stable_id="sha256:fn_login", hop=1, frequency_ratio=1.0, avg_ms=28.0)],
        side_effects=[],
        dynamic_only_edges=[],
        never_observed_static_edges=[],
        timing_p50_ms=28.0,
        timing_p99_ms=28.0,
    )
    defaults.update(kwargs)
    return ExecutionPath(**defaults)


def test_emit_calls_xadd_on_correct_stream():
    from app.emitter import emit_execution_path
    mock_redis = MagicMock()
    ep = _make_ep()
    emit_execution_path(mock_redis, ep)
    mock_redis.xadd.assert_called_once()
    stream_name, payload = mock_redis.xadd.call_args[0]
    assert stream_name == "stream:execution-paths"


def test_emit_serializes_execution_path():
    from app.emitter import emit_execution_path
    mock_redis = MagicMock()
    ep = _make_ep()
    emit_execution_path(mock_redis, ep)
    _, payload = mock_redis.xadd.call_args[0]
    parsed = json.loads(payload["event"])
    assert parsed["entrypoint_stable_id"] == "sha256:fn_test_login"
    assert parsed["call_sequence"][0]["stable_id"] == "sha256:fn_login"


def test_emit_returns_stream_id():
    from app.emitter import emit_execution_path
    mock_redis = MagicMock()
    mock_redis.xadd.return_value = b"1234-0"
    ep = _make_ep()
    result = emit_execution_path(mock_redis, ep)
    assert result == b"1234-0"
