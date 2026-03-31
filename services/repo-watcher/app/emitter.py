# services/repo-watcher/app/emitter.py
from __future__ import annotations

import redis

from .models import FileChangedEvent

STREAM = "stream:file-changed"


def emit_event(r: redis.Redis, event: FileChangedEvent):
    """Write a FileChangedEvent to the Redis stream. Returns the stream entry ID."""
    payload = {"event": event.model_dump_json()}
    return r.xadd(STREAM, payload)
