from __future__ import annotations
import asyncio
import json
import logging
from typing import Callable, Awaitable

import redis.asyncio as aioredis
from pydantic import ValidationError

from .cache import is_cached, mark_cached
from .coverage import get_coverage_filter
from .instrumenter_client import InstrumenterClient
from .models import EntrypointJob, RawTrace

logger = logging.getLogger(__name__)

STREAM_OUT = "stream:raw-traces"


async def _default_emit(r: aioredis.Redis, trace: RawTrace) -> None:
    await r.xadd(STREAM_OUT, {"event": trace.model_dump_json()})


async def process_job(
    job: EntrypointJob,
    commit_sha: str,
    r: aioredis.Redis,
    instrumenter: InstrumenterClient,
    emit_fn: Callable[[aioredis.Redis, RawTrace], Awaitable[None]] = _default_emit,
    repo_dir: str | None = None,
    capture_args: list[str] | None = None,
) -> str:
    """Process one EntrypointJob. Returns 'cached', 'ok', or 'error'."""
    if await is_cached(r, commit_sha, job.stable_id):
        logger.debug("Cache hit for %s @ %s", job.stable_id, commit_sha)
        return "cached"

    coverage_filter = None
    coverage_pct = None
    if repo_dir:
        coverage_filter, coverage_pct = await get_coverage_filter(
            r, repo=job.repo, commit_sha=commit_sha, repo_dir=repo_dir,
        )

    try:
        session_id = await instrumenter.instrument(
            stable_id=job.stable_id,
            file_path=job.file_path,
            repo=job.repo,
            capture_args=capture_args,
            coverage_filter=list(coverage_filter) if coverage_filter else None,
        )
        events, duration_ms = await instrumenter.run(session_id=session_id)
    except Exception as exc:
        logger.error("Instrumenter failed for %s: %s", job.stable_id, exc)
        return "error"

    trace = RawTrace(
        entrypoint_stable_id=job.stable_id,
        commit_sha=commit_sha,
        repo=job.repo,
        duration_ms=duration_ms,
        events=events,
        coverage_pct=coverage_pct,
    )
    await emit_fn(r, trace)
    await mark_cached(r, commit_sha, job.stable_id)
    return "ok"


async def run_worker(
    redis_url: str,
    instrumenter_url: str,
    commit_sha: str,
    repos: list[str],
) -> None:
    """Main BLPOP worker loop. Runs until cancelled."""
    r = aioredis.from_url(redis_url)
    instrumenter = InstrumenterClient(base_url=instrumenter_url)
    _discovered_keys: list[str] = []
    _last_scan: float = 0.0

    try:
        logger.info("Worker started, watching repos=%s", repos)
        while True:
            try:
                keys = [f"entrypoint_queue:{repo}" for repo in repos]
                if not keys:
                    # Re-scan Redis for entrypoint queues every 30 seconds
                    now = asyncio.get_event_loop().time()
                    if now - _last_scan >= 30.0 or not _discovered_keys:
                        _discovered_keys = []
                        async for key in r.scan_iter("entrypoint_queue:*"):
                            key_str = key.decode("utf-8") if isinstance(key, bytes) else key
                            _discovered_keys.append(key_str)
                        _last_scan = now
                        if _discovered_keys:
                            logger.info("Discovered queues: %s", _discovered_keys)
                    keys = _discovered_keys
                    if not keys:
                        await asyncio.sleep(5)
                        continue

                result = await r.blpop(keys, timeout=5)
                if result is None:
                    continue

                _key, raw = result
                try:
                    job = EntrypointJob.model_validate_json(
                        raw.decode("utf-8") if isinstance(raw, bytes) else raw
                    )
                except (ValidationError, json.JSONDecodeError) as exc:
                    logger.warning("Bad job message, skipping: %s", exc)
                    continue

                try:
                    outcome = await asyncio.wait_for(
                        process_job(
                            job=job,
                            commit_sha=commit_sha,
                            r=r,
                            instrumenter=instrumenter,
                        ),
                        timeout=30.0,
                    )
                    logger.info("Job %s: %s", job.stable_id, outcome)
                except asyncio.TimeoutError:
                    logger.error("Job timed out after 30s: %s", job.stable_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Worker loop error: %s", exc)
                await asyncio.sleep(1)
    finally:
        await r.aclose()
        await instrumenter.aclose()
