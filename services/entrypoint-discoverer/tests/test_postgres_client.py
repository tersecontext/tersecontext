# tests/test_postgres_client.py
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from app.postgres_client import query_last_traced


def _make_conn(rows):
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    cursor.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


def test_query_last_traced_returns_dict():
    ts = datetime(2026, 3, 1, tzinfo=timezone.utc)
    conn, _ = _make_conn([("id_a", ts), ("id_b", ts)])
    result = query_last_traced(conn, "myrepo")
    assert isinstance(result, dict)
    assert result["id_a"] == ts
    assert result["id_b"] == ts


def test_query_last_traced_passes_repo():
    conn, cursor = _make_conn([])
    query_last_traced(conn, "myrepo")
    call_args = cursor.execute.call_args
    assert "myrepo" in str(call_args) or call_args[0][1] == ("myrepo",)


def test_query_last_traced_empty_table():
    conn, _ = _make_conn([])
    result = query_last_traced(conn, "myrepo")
    assert result == {}
