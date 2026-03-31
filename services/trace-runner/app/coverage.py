from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

CACHE_TTL = 86400  # 24 hours
COVERAGE_TIMEOUT = 120  # seconds


def _cache_key(repo: str, commit_sha: str) -> str:
    return f"coverage:{repo}:{commit_sha}"


def _read_coverage_json(repo_dir: str) -> dict | None:
    """Read the coverage.json file produced by pytest --cov."""
    cov_path = os.path.join(repo_dir, "coverage.json")
    if not os.path.exists(cov_path):
        return None
    with open(cov_path) as f:
        return json.load(f)


def _parse_coverage(data: dict) -> tuple[set[str], float | None]:
    """Extract file set and coverage percentage from coverage.py JSON output."""
    files: set[str] = set()
    for filepath, info in data.get("files", {}).items():
        executed = info.get("executed_lines", [])
        if executed:
            files.add(filepath)
    pct = data.get("totals", {}).get("percent_covered")
    return files, pct


def _run_pytest_cov(repo_dir: str) -> bool:
    """Run pytest --cov in the repo directory. Returns True on success."""
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "--cov", "--cov-report=json", "-q", "--no-header"],
            cwd=repo_dir,
            capture_output=True,
            timeout=COVERAGE_TIMEOUT,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("pytest --cov failed: %s", exc)
        return False


async def get_coverage_filter(
    r: aioredis.Redis,
    repo: str,
    commit_sha: str,
    repo_dir: str,
) -> tuple[Optional[set[str]], Optional[float]]:
    """Get coverage filter for a repo@commit. Uses Redis cache, falls back to running pytest."""
    key = _cache_key(repo, commit_sha)

    cached = await r.get(key)
    if cached is not None:
        data = json.loads(cached)
        return set(data["files"]), data.get("coverage_pct")

    success = _run_pytest_cov(repo_dir)
    if not success:
        logger.warning("Coverage pre-pass failed for %s@%s, tracing all files", repo, commit_sha)
        return None, None

    cov_data = _read_coverage_json(repo_dir)
    if cov_data is None:
        logger.warning("No coverage.json found for %s@%s", repo, commit_sha)
        return None, None

    files, pct = _parse_coverage(cov_data)

    cache_value = json.dumps({"files": sorted(files), "coverage_pct": pct})
    await r.set(key, cache_value, ex=CACHE_TTL)

    return files, pct
