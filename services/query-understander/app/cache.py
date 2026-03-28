import hashlib
from typing import Optional

import redis.asyncio as redis

from .models import QueryIntent

CACHE_TTL = 3600


def _make_key(question: str, repo: str) -> str:
    raw = question.lower().strip() + "|" + repo
    sha = hashlib.sha256(raw.encode()).hexdigest()
    return f"query_intent:{sha}"


async def cache_get(client: redis.Redis, question: str, repo: str) -> Optional[QueryIntent]:
    key = _make_key(question, repo)
    data = await client.get(key)
    if data is None:
        return None
    return QueryIntent.model_validate_json(data)


async def cache_set(client: redis.Redis, question: str, repo: str, intent: QueryIntent) -> None:
    key = _make_key(question, repo)
    await client.setex(key, CACHE_TTL, intent.model_dump_json())
