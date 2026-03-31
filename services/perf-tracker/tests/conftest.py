import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

os.environ.setdefault("POSTGRES_DSN", "postgres://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")


@pytest.fixture
def sample_perf_metrics():
    from app.models import PerfMetric
    return [
        PerfMetric(
            metric_type="pipeline", metric_name="stream_throughput",
            entity_id="stream:raw-traces", repo="test",
            value=42.5, unit="msg/s",
        ),
        PerfMetric(
            metric_type="user_code", metric_name="fn_duration",
            entity_id="sha256:fn_auth", repo="test",
            value=120.0, unit="ms", commit_sha="abc123",
        ),
    ]


@pytest.fixture
def sample_raw_trace_data():
    import json
    return {
        b"event": json.dumps({
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
        }).encode()
    }


@pytest.fixture
def sample_execution_path_data():
    import json
    return {
        b"event": json.dumps({
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
        }).encode()
    }


@pytest.fixture
def mock_redis():
    m = AsyncMock()
    m.ping.return_value = True
    m.hset = AsyncMock()
    m.hgetall = AsyncMock(return_value={})
    m.zadd = AsyncMock()
    m.zrevrangebyscore = AsyncMock(return_value=[])
    m.zrevrange = AsyncMock(return_value=[])
    m.delete = AsyncMock()
    m.rpush = AsyncMock()
    m.lrange = AsyncMock(return_value=[])
    m.expire = AsyncMock()
    m.xlen = AsyncMock(return_value=0)
    m.llen = AsyncMock(return_value=0)
    return m


@pytest.fixture
def mock_pg_pool():
    pool = AsyncMock()
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.executemany = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=1)
    # transaction context manager
    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    # Make pool.acquire() work as async context manager
    pool.acquire = MagicMock(return_value=pool)
    pool.__aenter__ = AsyncMock(return_value=conn)
    pool.__aexit__ = AsyncMock(return_value=False)
    pool._conn = conn  # expose for assertions
    return pool


@pytest.fixture
def mock_store(mock_redis, mock_pg_pool):
    from app.store import PerfStore
    store = PerfStore.__new__(PerfStore)
    store._redis = mock_redis
    store._pool = mock_pg_pool
    store._pool_lock = __import__("asyncio").Lock()
    store._postgres_dsn = "postgres://test:test@localhost/test"
    return store
