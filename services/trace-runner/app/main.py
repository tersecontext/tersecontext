from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO)

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from shared.service import ServiceBase
from .runner import stats as runner_stats

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
_svc = ServiceBase("trace-runner", VERSION)
_worker_task: asyncio.Task | None = None


async def _start_worker() -> asyncio.Task:
    from .runner import run_worker

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    instrumenter_url = os.environ.get("INSTRUMENTER_URL", "http://localhost:8093")
    go_instrumenter_url = os.environ.get("GO_INSTRUMENTER_URL", "")
    go_trace_runner_url = os.environ.get("GO_TRACE_RUNNER_URL", "")
    commit_sha = os.environ.get("COMMIT_SHA", "unknown")
    repos_raw = os.environ.get("REPOS", "")
    repos = [r.strip() for r in repos_raw.split(",") if r.strip()]

    return asyncio.create_task(
        run_worker(
            redis_url=redis_url,
            instrumenter_url=instrumenter_url,
            commit_sha=commit_sha,
            repos=repos,
            go_instrumenter_url=go_instrumenter_url or None,
            go_trace_runner_url=go_trace_runner_url or None,
        )
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _worker_task
    _svc._dep_checkers.clear()  # idempotent restart safety

    async def check_redis() -> str | None:
        try:
            await _svc.get_redis().ping()
            return None
        except Exception as exc:
            return f"redis: {exc}"

    _svc.add_dep_checker(check_redis)
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
        await _svc.close_redis()


app = FastAPI(lifespan=lifespan)
app.include_router(_svc.router)


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP trace_runner_jobs_processed_total Total jobs processed",
        "# TYPE trace_runner_jobs_processed_total counter",
        f"trace_runner_jobs_processed_total {runner_stats.jobs_processed}",
        "# HELP trace_runner_jobs_cached_total Total jobs skipped (cache hit)",
        "# TYPE trace_runner_jobs_cached_total counter",
        f"trace_runner_jobs_cached_total {runner_stats.jobs_cached}",
        "# HELP trace_runner_jobs_failed_total Total jobs failed",
        "# TYPE trace_runner_jobs_failed_total counter",
        f"trace_runner_jobs_failed_total {runner_stats.jobs_failed}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
