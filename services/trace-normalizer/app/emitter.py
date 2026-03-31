from __future__ import annotations
import redis
from .models import ExecutionPath

STREAM = "stream:execution-paths"


def emit_execution_path(r: redis.Redis, ep: ExecutionPath):
    """Write an ExecutionPath to the Redis stream. Returns the stream entry ID."""
    payload = {"event": ep.model_dump_json()}
    return r.xadd(STREAM, payload)
