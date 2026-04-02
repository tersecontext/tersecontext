# services/repo-watcher/app/emitter.py
from __future__ import annotations

import redis

from .models import FileChangedEvent

STREAM = "stream:file-changed"


def emit_event(r: redis.Redis, event: FileChangedEvent):
    """Write a FileChangedEvent to the Redis stream. Returns the stream entry ID."""
    payload = {"event": event.model_dump_json()}
    return r.xadd(STREAM, payload)


STREAM_REPO_INDEXED = "stream:repo-indexed"


def emit_repo_indexed(r: redis.Redis, repo: str, path: str, commit_sha: str) -> None:
    """Emit a repo-indexed event after all file-changed events for a commit."""
    r.xadd(STREAM_REPO_INDEXED, {
        "repo": repo,
        "path": path,
        "commit_sha": commit_sha,
    })
