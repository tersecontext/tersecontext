# services/repo-watcher/app/watcher.py
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from datetime import datetime, timezone

import redis

from .differ import detect_language, get_all_files, get_changed_files, get_changed_nodes, git_show, parse_nodes
from .emitter import emit_event
from .models import FileChangedEvent

logger = logging.getLogger(__name__)

SHA_KEY_PREFIX = "last_indexed_sha:"
AT_KEY_PREFIX = "last_indexed_at:"


def _get_last_sha(r: redis.Redis, repo: str) -> str | None:
    val = r.get(f"{SHA_KEY_PREFIX}{repo}")
    return val.decode() if val else None


def _get_last_indexed_at(r: redis.Redis, repo: str) -> str | None:
    val = r.get(f"{AT_KEY_PREFIX}{repo}")
    return val.decode() if val else None


def _set_last_sha(r: redis.Redis, repo: str, sha: str) -> None:
    r.set(f"{SHA_KEY_PREFIX}{repo}", sha)
    r.set(f"{AT_KEY_PREFIX}{repo}", datetime.now(timezone.utc).isoformat())


def _repo_name(repo_path: str) -> str:
    return os.path.basename(repo_path.rstrip("/"))


def process_commit(
    r: redis.Redis,
    repo_path: str,
    prev_sha: str | None,
    current_sha: str,
) -> tuple[int, int]:
    """
    Compute diff and emit FileChanged events.
    Returns (events_emitted, files_changed).
    Raises on Redis errors so caller can skip SHA update.
    """
    repo = _repo_name(repo_path)
    events_emitted = 0

    if prev_sha is None:
        # First run — full rescan: emit all supported files with diff_type=full_rescan
        files = get_all_files(repo_path)
        for file_path in files:
            language = detect_language(file_path)
            if language is None:
                continue
            content = git_show(repo_path, current_sha, file_path)
            nodes = parse_nodes(content, file_path, repo)
            event = FileChangedEvent(
                repo=repo,
                commit_sha=current_sha,
                path=file_path,
                language=language,
                diff_type="full_rescan",
                changed_nodes=[],
                added_nodes=[n.stable_id for n in nodes],
                deleted_nodes=[],
            )
            emit_event(r, event)
            events_emitted += 1
        return events_emitted, len(files)

    # Incremental diff
    changed_files = get_changed_files(repo_path, prev_sha, current_sha)
    for entry in changed_files:
        file_path = entry["path"]
        diff_type = entry["diff_type"]
        language = detect_language(file_path)
        if language is None:
            continue

        if diff_type == "added":
            content = git_show(repo_path, current_sha, file_path)
            nodes = parse_nodes(content, file_path, repo)
            event = FileChangedEvent(
                repo=repo,
                commit_sha=current_sha,
                path=file_path,
                language=language,
                diff_type="added",
                changed_nodes=[],
                added_nodes=[n.stable_id for n in nodes],
                deleted_nodes=[],
            )
        elif diff_type == "deleted":
            content = git_show(repo_path, prev_sha, file_path)
            nodes = parse_nodes(content, file_path, repo)
            event = FileChangedEvent(
                repo=repo,
                commit_sha=current_sha,
                path=file_path,
                language=language,
                diff_type="deleted",
                changed_nodes=[],
                added_nodes=[],
                deleted_nodes=[n.stable_id for n in nodes],
            )
        else:  # modified
            changed_nodes, added_nodes, deleted_nodes = get_changed_nodes(
                repo_path, file_path, prev_sha, current_sha, repo
            )
            event = FileChangedEvent(
                repo=repo,
                commit_sha=current_sha,
                path=file_path,
                language=language,
                diff_type="modified",
                changed_nodes=changed_nodes,
                added_nodes=added_nodes,
                deleted_nodes=deleted_nodes,
            )

        emit_event(r, event)
        events_emitted += 1

    return events_emitted, len(changed_files)


async def run_watcher(repo_path: str, r: redis.Redis, poll_interval: int) -> None:
    """Background polling loop. Runs until cancelled."""
    repo = _repo_name(repo_path)
    logger.info("Watcher started for %s (poll every %ds)", repo_path, poll_interval)
    while True:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                logger.warning("git rev-parse failed: %s", result.stderr.strip())
                await asyncio.sleep(poll_interval)
                continue

            current_sha = result.stdout.strip()
            prev_sha = _get_last_sha(r, repo)

            if current_sha != prev_sha:
                logger.info("New SHA detected: %s -> %s", prev_sha, current_sha)
                try:
                    events, files = process_commit(r, repo_path, prev_sha, current_sha)
                    _set_last_sha(r, repo, current_sha)
                    logger.info("Emitted %d events for %d files", events, files)
                except Exception as exc:
                    logger.error("Error processing commit %s: %s", current_sha, exc)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Watcher loop error: %s", exc)

        await asyncio.sleep(poll_interval)
