import asyncio
import logging
import os
from contextlib import asynccontextmanager

import redis as redis_lib
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "parser"

_redis_client: redis_lib.Redis | None = None


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = redis_lib.from_url(url)
    return _redis_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .consumer import run_consumer
    task = asyncio.create_task(run_consumer())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE, "version": VERSION}


@app.get("/ready")
def ready():
    try:
        _get_redis().ping()
        return {"status": "ok"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable"})


@app.get("/metrics")
def metrics():
    # Stub: returns hardcoded zeros. Wire real counters when metrics matter.
    lines = [
        "# HELP parser_messages_processed_total Total messages processed",
        "# TYPE parser_messages_processed_total counter",
        "parser_messages_processed_total 0",
        "# HELP parser_messages_failed_total Total messages failed",
        "# TYPE parser_messages_failed_total counter",
        "parser_messages_failed_total 0",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
