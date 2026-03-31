# services/repo-watcher/tests/test_emitter.py
import json
from unittest.mock import MagicMock

from app.models import FileChangedEvent


def _make_event(**kwargs):
    defaults = dict(
        repo="test-repo",
        commit_sha="abc123",
        path="auth.py",
        language="python",
        diff_type="modified",
        changed_nodes=["sha256:aaa"],
        added_nodes=[],
        deleted_nodes=[],
    )
    defaults.update(kwargs)
    return FileChangedEvent(**defaults)


def test_emit_event_calls_xadd():
    from app.emitter import emit_event
    mock_redis = MagicMock()
    event = _make_event()
    emit_event(mock_redis, event)
    mock_redis.xadd.assert_called_once()
    stream_name, payload = mock_redis.xadd.call_args[0]
    assert stream_name == "stream:file-changed"
    parsed = json.loads(payload["event"])
    assert parsed["repo"] == "test-repo"
    assert parsed["diff_type"] == "modified"


def test_emit_event_serializes_full_rescan():
    from app.emitter import emit_event
    mock_redis = MagicMock()
    event = _make_event(diff_type="full_rescan", changed_nodes=[], added_nodes=["sha256:x"])
    emit_event(mock_redis, event)
    payload = mock_redis.xadd.call_args[0][1]
    parsed = json.loads(payload["event"])
    assert parsed["diff_type"] == "full_rescan"
    assert parsed["added_nodes"] == ["sha256:x"]


def test_emit_event_returns_stream_id():
    from app.emitter import emit_event
    mock_redis = MagicMock()
    mock_redis.xadd.return_value = b"1234-0"
    event = _make_event()
    result = emit_event(mock_redis, event)
    assert result == b"1234-0"
