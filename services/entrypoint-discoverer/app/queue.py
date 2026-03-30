# app/queue.py
from __future__ import annotations
import json
import redis

from .models import EntrypointJob


def push_jobs(r: redis.Redis, repo: str, jobs: list[EntrypointJob]) -> int:
    """RPUSH each job as JSON to entrypoint_queue:{repo}. Returns count pushed."""
    key = f"entrypoint_queue:{repo}"
    for job in jobs:
        r.rpush(key, json.dumps(job.model_dump()))
    return len(jobs)
