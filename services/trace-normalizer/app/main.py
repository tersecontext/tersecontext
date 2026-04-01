# services/trace-normalizer/app/main.py
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import redis as redis_lib
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from shared.service import ServiceBase

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

VERSION = "0.1.0"
_svc = ServiceBase("trace-normalizer", VERSION)

_redis_client: redis_lib.Redis | None = None
_neo4j_driver = None
_consumer = None  # NormalizerConsumer | None


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = redis_lib.from_url(url, decode_responses=False)
    return _redis_client


def _get_neo4j_driver():
    global _neo4j_driver
    if _neo4j_driver is None:
        neo4j_url = os.environ.get("NEO4J_URL", "")
        if not neo4j_url:
            return None
        try:
            from neo4j import GraphDatabase
            _neo4j_driver = GraphDatabase.driver(
                neo4j_url,
                auth=(
                    os.environ.get("NEO4J_USER", "neo4j"),
                    os.environ["NEO4J_PASSWORD"],
                ),
            )
        except Exception as exc:
            logger.warning("Neo4j driver init failed: %s", exc)
    return _neo4j_driver


def _process_message(r, driver, raw_json: str) -> None:
    """Backward-compat shim used by existing tests. New code uses NormalizerConsumer."""
    from .consumer import NormalizerConsumer
    NormalizerConsumer._process_sync(r, driver, raw_json)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _consumer
    from .consumer import NormalizerConsumer

    _svc._dep_checkers.clear()  # idempotent restart safety

    r_sync = _get_redis()
    driver = _get_neo4j_driver()

    async def check_redis() -> str | None:
        try:
            r_sync.ping()
            return None
        except Exception as exc:
            return f"redis: {exc}"

    _svc.add_dep_checker(check_redis)
    if driver is not None:
        async def check_neo4j() -> str | None:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, driver.verify_connectivity)
                return None
            except Exception as exc:
                return f"neo4j: {exc}"
        _svc.add_dep_checker(check_neo4j)

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    _consumer = NormalizerConsumer(r_sync, driver)
    consumer_task = asyncio.create_task(_consumer.run(redis_url))
    try:
        yield
    finally:
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass
        if _redis_client is not None:
            _redis_client.close()
        if _neo4j_driver is not None:
            _neo4j_driver.close()
        _consumer = None


app = FastAPI(lifespan=lifespan)
app.include_router(_svc.router)  # registers /health and /ready


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP trace_normalizer_events_processed_total Total ExecutionPath events emitted",
        "# TYPE trace_normalizer_events_processed_total counter",
        f"trace_normalizer_events_processed_total {_consumer.events_processed if _consumer else 0}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
