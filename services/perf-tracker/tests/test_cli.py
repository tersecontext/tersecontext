import pytest
import json
from unittest.mock import patch, MagicMock


@pytest.fixture
def mock_httpx_get():
    with patch("httpx.get") as m:
        yield m


@pytest.fixture
def mock_httpx_post():
    with patch("httpx.post") as m:
        yield m


def test_status_command(mock_httpx_get, capsys):
    mock_httpx_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"throughput": "42.5", "queue_depth:stream:raw-traces": "10"},
    )
    from cli import run_command
    run_command(["status", "test-repo"], host="localhost", port=8098)
    out = capsys.readouterr().out
    assert "throughput" in out
    assert "42.5" in out


def test_bottlenecks_command(mock_httpx_get, capsys):
    mock_httpx_get.return_value = MagicMock(
        status_code=200,
        json=lambda: [
            {"entity_id": "x", "category": "pipeline", "severity": "warning",
             "value": 15.2, "unit": "s", "detail": "Lag exceeds threshold"},
        ],
    )
    from cli import run_command
    run_command(["bottlenecks", "test-repo"], host="localhost", port=8098)
    out = capsys.readouterr().out
    assert "pipeline" in out
    assert "warning" in out


def test_slow_command(mock_httpx_get, capsys):
    mock_httpx_get.return_value = MagicMock(
        status_code=200,
        json=lambda: [
            {"entity_id": "sha256:fn", "value": 120.0, "unit": "ms", "commit_sha": "abc"},
        ],
    )
    from cli import run_command
    run_command(["slow", "test-repo", "--limit", "5"], host="localhost", port=8098)
    out = capsys.readouterr().out
    assert "120.0" in out


def test_trends_command(mock_httpx_get, capsys):
    mock_httpx_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "entity_id": "sha256:fn", "metric_name": "fn_duration", "window": "24h",
            "points": [{"value": 100.0, "recorded_at": "2026-01-01T00:00:00Z"}],
            "trend": {"slope": 0.5, "direction": "increasing"},
        },
    )
    from cli import run_command
    run_command(["trends", "test-repo", "sha256:fn", "--window", "24h"], host="localhost", port=8098)
    out = capsys.readouterr().out
    assert "increasing" in out


def test_regressions_command(mock_httpx_get, capsys):
    mock_httpx_get.return_value = MagicMock(
        status_code=200,
        json=lambda: [
            {"entity_id": "sha256:fn", "base_val": 100.0, "head_val": 150.0, "pct_change": 50.0},
        ],
    )
    from cli import run_command
    run_command(["regressions", "test-repo", "--base", "abc1234", "--head", "def5678"], host="localhost", port=8098)
    out = capsys.readouterr().out
    assert "50.0" in out


def test_summary_command(mock_httpx_get, capsys):
    mock_httpx_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "repo": "test-repo",
            "pipeline_health": {},
            "top_slow_functions": [],
            "active_bottlenecks": [],
            "metric_counts": {"pipeline": 10},
        },
    )
    from cli import run_command
    run_command(["summary", "test-repo"], host="localhost", port=8098)
    out = capsys.readouterr().out
    assert "test-repo" in out


def test_json_output(mock_httpx_get, capsys):
    data = {"throughput": "42.5"}
    mock_httpx_get.return_value = MagicMock(status_code=200, json=lambda: data)
    from cli import run_command
    run_command(["status", "test-repo", "--json"], host="localhost", port=8098)
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["throughput"] == "42.5"
