import asyncio
import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api import router
from app import collector as _collector

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "perf-tracker"

_redis_client: aioredis.Redis | None = None
_store = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = aioredis.from_url(url)
    return _redis_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _store

    from app.store import PerfStore
    from app.collector import run_collector

    postgres_dsn = os.environ["POSTGRES_DSN"]
    redis_client = _get_redis()

    _store = PerfStore(postgres_dsn, redis_client)
    await _store.ensure_schema()

    task = asyncio.create_task(run_collector(_store, redis_client))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await _store.close()


app = FastAPI(lifespan=lifespan)
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE, "version": VERSION}


@app.get("/ready")
async def ready():
    errors = []
    try:
        await _get_redis().ping()
    except Exception as exc:
        errors.append(f"redis: {exc}")
    try:
        if _store:
            pool = await _store._get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
    except Exception as exc:
        errors.append(f"postgres: {exc}")
    if errors:
        return JSONResponse(status_code=503, content={"status": "unavailable", "errors": errors})
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP perf_tracker_messages_processed_total Total stream messages processed",
        "# TYPE perf_tracker_messages_processed_total counter",
        f"perf_tracker_messages_processed_total {_collector.messages_processed_total}",
        "# HELP perf_tracker_messages_failed_total Total stream messages failed",
        "# TYPE perf_tracker_messages_failed_total counter",
        f"perf_tracker_messages_failed_total {_collector.messages_failed_total}",
        "# HELP perf_tracker_metrics_written_total Total metrics written to Postgres",
        "# TYPE perf_tracker_metrics_written_total counter",
        f"perf_tracker_metrics_written_total {_collector.metrics_written_total}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
