# tests/test_discoverer.py
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from app.discoverer import run_discover


def test_run_discover_returns_discovered_and_queued():
    entrypoints = [
        {"stable_id": "id_a", "name": "test_login", "file_path": "tests/test_auth.py"},
        {"stable_id": "id_b", "name": "test_signup", "file_path": "tests/test_auth.py"},
    ]
    neo4j_driver = MagicMock()
    pg_conn = MagicMock()
    redis_client = MagicMock()

    with patch("app.discoverer.query_entrypoints", return_value=entrypoints), \
         patch("app.discoverer.query_changed_dep_ids", return_value=set()), \
         patch("app.discoverer.query_last_traced", return_value={}), \
         patch("app.discoverer.push_jobs", return_value=2) as mock_push:

        result = run_discover(neo4j_driver, pg_conn, redis_client, repo="r", trigger="schedule")

    assert result["discovered"] == 2
    assert result["queued"] == 2


def test_run_discover_skips_queuing_empty_entrypoints():
    neo4j_driver = MagicMock()
    pg_conn = MagicMock()
    redis_client = MagicMock()

    with patch("app.discoverer.query_entrypoints", return_value=[]), \
         patch("app.discoverer.query_changed_dep_ids", return_value=set()), \
         patch("app.discoverer.query_last_traced", return_value={}), \
         patch("app.discoverer.push_jobs", return_value=0) as mock_push:

        result = run_discover(neo4j_driver, pg_conn, redis_client, repo="r", trigger="schedule")

    assert result["discovered"] == 0
    assert result["queued"] == 0


def test_run_discover_passes_repo_to_all_clients():
    neo4j_driver = MagicMock()
    pg_conn = MagicMock()
    redis_client = MagicMock()

    with patch("app.discoverer.query_entrypoints", return_value=[]) as mock_ep, \
         patch("app.discoverer.query_changed_dep_ids", return_value=set()) as mock_cd, \
         patch("app.discoverer.query_last_traced", return_value={}) as mock_lt, \
         patch("app.discoverer.push_jobs", return_value=0):

        run_discover(neo4j_driver, pg_conn, redis_client, repo="target-repo", trigger="pr_open")

    mock_ep.assert_called_once_with(neo4j_driver, "target-repo")
    mock_lt.assert_called_once_with(pg_conn, "target-repo")
    assert mock_cd.call_args[0][1] == "target-repo"
