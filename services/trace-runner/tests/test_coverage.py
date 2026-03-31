import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_coverage_prepass_cache_miss():
    """On cache miss, runs pytest --cov, parses JSON, caches result."""
    from app.coverage import get_coverage_filter

    coverage_json = {
        "files": {
            "src/auth.py": {"executed_lines": [1, 2, 3]},
            "src/db.py": {"executed_lines": [10, 20]},
            "src/unused.py": {"executed_lines": []},
        },
        "totals": {"percent_covered": 72.5},
    }

    mock_redis = AsyncMock()
    mock_redis.get.return_value = None  # cache miss

    with patch("app.coverage._run_pytest_cov", return_value=True), \
         patch("app.coverage._read_coverage_json", return_value=coverage_json):
        files, pct = await get_coverage_filter(
            mock_redis, repo="test", commit_sha="abc123", repo_dir="/tmp/repo",
        )

    assert "src/auth.py" in files
    assert "src/db.py" in files
    assert "src/unused.py" not in files
    assert pct == 72.5
    mock_redis.set.assert_called_once()


@pytest.mark.asyncio
async def test_coverage_prepass_cache_hit():
    """On cache hit, returns cached data without running pytest."""
    from app.coverage import get_coverage_filter

    cached = json.dumps({"files": ["src/auth.py", "src/db.py"], "coverage_pct": 72.5})
    mock_redis = AsyncMock()
    mock_redis.get.return_value = cached.encode()

    files, pct = await get_coverage_filter(
        mock_redis, repo="test", commit_sha="abc123", repo_dir="/tmp/repo",
    )

    assert files == {"src/auth.py", "src/db.py"}
    assert pct == 72.5


@pytest.mark.asyncio
async def test_coverage_prepass_failure_returns_none():
    """If pytest --cov fails, returns (None, None)."""
    from app.coverage import get_coverage_filter

    mock_redis = AsyncMock()
    mock_redis.get.return_value = None

    with patch("app.coverage._run_pytest_cov", return_value=False):
        files, pct = await get_coverage_filter(
            mock_redis, repo="test", commit_sha="abc123", repo_dir="/tmp/repo",
        )

    assert files is None
    assert pct is None
