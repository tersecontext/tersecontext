# services/symbol-resolver/app/main.py
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

from .consumer import run_consumer
from .neo4j_client import make_driver

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "symbol-resolver"

_redis_client: aioredis.Redis | None = None
_driver = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = aioredis.from_url(url)
    return _redis_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _driver
    _driver = make_driver()
    task = asyncio.create_task(run_consumer(_driver))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if _driver:
            _driver.close()
        if _redis_client is not None:
            await _redis_client.aclose()


app = FastAPI(lifespan=lifespan)


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
    if _driver is None:
        errors.append("neo4j: driver not initialized")
    else:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _driver.verify_connectivity)
        except Exception as exc:
            errors.append(f"neo4j: {exc}")
    if errors:
        return JSONResponse(status_code=503, content={"status": "unavailable", "errors": errors})
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP symbol_resolver_messages_processed_total Total messages processed",
        "# TYPE symbol_resolver_messages_processed_total counter",
        "symbol_resolver_messages_processed_total 0",
        "# HELP symbol_resolver_imports_resolved_total Total IMPORTS edges written",
        "# TYPE symbol_resolver_imports_resolved_total counter",
        "symbol_resolver_imports_resolved_total 0",
        "# HELP symbol_resolver_pending_refs_total Total refs pushed to pending queue",
        "# TYPE symbol_resolver_pending_refs_total counter",
        "symbol_resolver_pending_refs_total 0",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
