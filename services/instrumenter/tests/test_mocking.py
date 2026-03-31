import pytest
from unittest.mock import MagicMock
from app.mocking import create_mock_patches, IOEvent


def test_db_mock_logs_sql():
    """DB mock patches log SQL and return mock cursor."""
    events: list[IOEvent] = []
    patches = create_mock_patches(events, tempdir="/tmp/test")

    db_patch = next(p for p in patches if "Session.execute" in p["target"])
    mock_fn = db_patch["side_effect"]
    result = mock_fn("SELECT * FROM users WHERE id = 1")
    assert len(events) == 1
    assert events[0].action == "mock_db"
    assert "SELECT" in events[0].detail


def test_http_mock_logs_request():
    """HTTP mock patches log method+url and return 200."""
    events: list[IOEvent] = []
    patches = create_mock_patches(events, tempdir="/tmp/test")

    http_patch = next(p for p in patches if "httpx.Client.request" in p["target"])
    mock_fn = http_patch["side_effect"]
    result = mock_fn("GET", "https://api.example.com/users")
    assert len(events) == 1
    assert events[0].action == "mock_http"
    assert "api.example.com" in events[0].detail
    assert result.status_code == 200


def test_file_write_redirected():
    """File open in write mode redirects to tempdir."""
    import os
    import tempfile

    events: list[IOEvent] = []
    with tempfile.TemporaryDirectory() as td:
        patches = create_mock_patches(events, tempdir=td)
        open_patch = next(p for p in patches if "builtins.open" in p["target"])
        mock_fn = open_patch["side_effect"]
        fh = mock_fn("/etc/important.conf", "w")
        fh.write("test data")
        fh.close()
        assert len(events) == 1
        assert events[0].action == "redirect_writes"
        redirected_files = os.listdir(td)
        assert len(redirected_files) >= 1
