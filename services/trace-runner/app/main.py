from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "trace-runner"

_redis_client: aioredis.Redis | None = None
_worker_task: asyncio.Task | None = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = aioredis.from_url(url)
    return _redis_client


async def _start_worker() -> asyncio.Task:
    from .runner import run_worker

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    instrumenter_url = os.environ.get("INSTRUMENTER_URL", "http://localhost:8093")
    commit_sha = os.environ.get("COMMIT_SHA", "unknown")
    repos_raw = os.environ.get("REPOS", "")
    repos = [r.strip() for r in repos_raw.split(",") if r.strip()]

    return asyncio.create_task(
        run_worker(
            redis_url=redis_url,
            instrumenter_url=instrumenter_url,
            commit_sha=commit_sha,
            repos=repos,
        )
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _worker_task
    _worker_task = await _start_worker()
    try:
        yield
    finally:
        if _worker_task:
            _worker_task.cancel()
            try:
                await _worker_task
            except asyncio.CancelledError:
                pass
        if _redis_client is not None:
            await _redis_client.aclose()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE, "version": VERSION}


@app.get("/ready")
async def ready():
    try:
        await _get_redis().ping()
        return {"status": "ok"}
    except Exception as exc:
        return JSONResponse(status_code=503, content={"status": "unavailable", "error": "redis unavailable"})


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP trace_runner_jobs_processed_total Total jobs processed",
        "# TYPE trace_runner_jobs_processed_total counter",
        "trace_runner_jobs_processed_total 0",
        "# HELP trace_runner_jobs_cached_total Total jobs skipped (cache hit)",
        "# TYPE trace_runner_jobs_cached_total counter",
        "trace_runner_jobs_cached_total 0",
        "# HELP trace_runner_jobs_failed_total Total jobs failed",
        "# TYPE trace_runner_jobs_failed_total counter",
        "trace_runner_jobs_failed_total 0",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
