from __future__ import annotations
import asyncio
import logging
import os
import socket
import time

import redis.asyncio as aioredis
import redis.exceptions

from .indexer import index_repo

logger = logging.getLogger(__name__)

STREAM = "stream:repo-indexed"
GROUP = "lsp-indexer-group"
READY_KEY_TEMPLATE = "graph-writer:repo-ready:{repo}:{commit_sha}"


def get_redis(url: str) -> aioredis.Redis:
    return aioredis.from_url(url)


async def process_message(
    r,
    repo_b: bytes,
    path_b: bytes,
    commit_sha_b: bytes,
    language_servers: dict,
    driver,
    poll_interval: float = 1.0,
    poll_timeout: float = 60.0,
) -> None:
    repo = repo_b.decode() if isinstance(repo_b, bytes) else repo_b
    path = path_b.decode() if isinstance(path_b, bytes) else path_b
    commit_sha = commit_sha_b.decode() if isinstance(commit_sha_b, bytes) else commit_sha_b

    ready_key = READY_KEY_TEMPLATE.format(repo=repo, commit_sha=commit_sha)
    deadline = time.monotonic() + poll_timeout
    while time.monotonic() < deadline:
        val = await r.get(ready_key)
        if val:
            logger.info("Readiness key found: %s", ready_key)
            break
        if poll_interval > 0:
            await asyncio.sleep(poll_interval)
        else:
            break
    else:
        logger.warning("Readiness key not found within timeout, proceeding: %s", ready_key)

    await index_repo(repo, path, language_servers, driver)


async def run_consumer(language_servers: dict, driver) -> None:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = get_redis(url)
    consumer_name = f"lsp-indexer-{socket.gethostname()}"

    try:
        try:
            await r.xgroup_create(STREAM, GROUP, id="$", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        logger.info("LSP indexer consumer started: group=%s", GROUP)

        while True:
            try:
                messages = await r.xreadgroup(
                    groupname=GROUP,
                    consumername=consumer_name,
                    streams={STREAM: ">"},
                    count=1,
                    block=1000,
                )
                for _stream, events in (messages or []):
                    for msg_id, data in events:
                        try:
                            repo = data.get(b"repo") or data.get("repo", b"")
                            path = data.get(b"path") or data.get("path", b"")
                            commit_sha = data.get(b"commit_sha") or data.get("commit_sha", b"")
                            if repo:
                                await process_message(r, repo, path, commit_sha, language_servers, driver)
                            await r.xack(STREAM, GROUP, msg_id)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.error("Consumer error msg=%s: %s", msg_id, exc)
                            await r.xack(STREAM, GROUP, msg_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Consumer loop error: %s", exc)
    finally:
        await r.aclose()
