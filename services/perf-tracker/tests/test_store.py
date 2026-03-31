import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone


async def test_ensure_schema(mock_store, mock_pg_pool):
    await mock_store.ensure_schema()
    conn = mock_pg_pool._conn
    conn.execute.assert_called_once()
    sql = conn.execute.call_args[0][0]
    assert "perf_metrics" in sql
    assert "CREATE TABLE" in sql


async def test_write_metrics(mock_store, mock_pg_pool, sample_perf_metrics):
    await mock_store.write_metrics(sample_perf_metrics)
    conn = mock_pg_pool._conn
    assert conn.executemany.called
    args = conn.executemany.call_args
    assert "INSERT INTO perf_metrics" in args[0][0]
    assert len(args[0][1]) == 2


async def test_update_realtime(mock_store, mock_redis):
    snapshot = {"throughput_raw_traces": "42.5", "queue_depth_raw_traces": "10"}
    await mock_store.update_realtime("test", snapshot)
    mock_redis.delete.assert_called_once_with("perf:realtime:test")
    mock_redis.hset.assert_called_once_with("perf:realtime:test", mapping=snapshot)


async def test_update_hot_functions(mock_store, mock_redis):
    fns = {"sha256:fn_auth": 120.0, "sha256:fn_login": 85.0}
    await mock_store.update_hot_functions("test", fns)
    mock_redis.delete.assert_called()
    mock_redis.zadd.assert_called_once_with("perf:hotfns:test", fns)


async def test_get_realtime(mock_store, mock_redis):
    mock_redis.hgetall.return_value = {b"throughput": b"42.5"}
    result = await mock_store.get_realtime("test")
    assert result == {"throughput": "42.5"}


async def test_get_slow_functions(mock_store, mock_pg_pool):
    mock_row = {"entity_id": "sha256:fn", "value": 120.0, "unit": "ms", "commit_sha": "abc"}
    mock_pg_pool._conn.fetch.return_value = [mock_row]
    rows = await mock_store.get_slow_functions("test", limit=10)
    assert len(rows) == 1
    assert rows[0]["value"] == 120.0


async def test_get_trends(mock_store, mock_pg_pool):
    mock_pg_pool._conn.fetch.return_value = [
        {"value": 100.0, "recorded_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        {"value": 110.0, "recorded_at": datetime(2026, 1, 2, tzinfo=timezone.utc)},
    ]
    rows = await mock_store.get_trends("test", "sha256:fn", "fn_duration", window_hours=24)
    assert len(rows) == 2
