from __future__ import annotations
import redis.asyncio as aioredis

CACHE_TTL_SECONDS = 86400  # 24 hours


def cache_key(commit_sha: str, stable_id: str) -> str:
    return f"trace_cache:{commit_sha}:{stable_id}"


async def is_cached(r: aioredis.Redis, commit_sha: str, stable_id: str) -> bool:
    return bool(await r.exists(cache_key(commit_sha, stable_id)))


async def mark_cached(r: aioredis.Redis, commit_sha: str, stable_id: str) -> None:
    await r.set(cache_key(commit_sha, stable_id), "1", ex=CACHE_TTL_SECONDS)
