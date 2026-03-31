# app/queue.py
from __future__ import annotations
import json
import redis

from .models import EntrypointJob


def queue_key(repo: str, language: str = "python") -> str:
    """Return the Redis list key for entrypoint jobs, language-aware."""
    key = f"entrypoint_queue:{repo}"
    if language == "go":
        key += ":go"
    return key


def push_jobs(r: redis.Redis, repo: str, jobs: list[EntrypointJob]) -> int:
    """RPUSH each job as JSON to the appropriate language queue. Returns count pushed."""
    for job in jobs:
        key = queue_key(repo, job.language)
        r.rpush(key, json.dumps(job.model_dump()))
    return len(jobs)
