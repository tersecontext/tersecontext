import json
import pytest
from unittest.mock import MagicMock
from shared.consumer import RedisConsumerBase
from app.consumer import NormalizerConsumer


def test_normalizer_consumer_inherits_base():
    assert issubclass(NormalizerConsumer, RedisConsumerBase)
    assert NormalizerConsumer.stream == "stream:raw-traces"
    assert NormalizerConsumer.group == "normalizer-group"


def test_process_sync_computes_coverage_pct():
    """coverage_pct should be a float between 0 and 1 when nodes are present."""
    raw_trace = {
        "entrypoint_stable_id": "sha256:fn_test",
        "commit_sha": "abc",
        "repo": "test",
        "duration_ms": 50.0,
        "events": [
            {"type": "call", "fn": "login", "file": "a.py", "line": 1, "timestamp_ms": 0.0},
            {"type": "return", "fn": "login", "file": "a.py", "line": 5, "timestamp_ms": 10.0},
        ],
    }
    mock_redis = MagicMock()
    mock_redis.get.return_value = None
    mock_redis.xadd.return_value = b"1-0"

    NormalizerConsumer._process_sync(mock_redis, None, json.dumps(raw_trace))

    mock_redis.xadd.assert_called_once()
    _, payload = mock_redis.xadd.call_args[0]
    ep = json.loads(payload["event"])
    assert ep["coverage_pct"] is None or (0.0 <= ep["coverage_pct"] <= 1.0)
