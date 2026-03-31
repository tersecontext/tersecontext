# services/graph-enricher/app/main.py
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from neo4j import GraphDatabase

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "graph-enricher"

_redis_client: aioredis.Redis | None = None
_driver = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = aioredis.from_url(url)
    return _redis_client


def _make_driver():
    url = os.environ.get("NEO4J_URL", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    return GraphDatabase.driver(url, auth=(user, password))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _driver
    from .consumer import run_consumer

    _driver = _make_driver()
    consumer_task = asyncio.create_task(run_consumer(_driver))
    try:
        yield
    finally:
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass
        if _driver:
            _driver.close()


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
        "# HELP graph_enricher_messages_processed_total Total messages processed",
        "# TYPE graph_enricher_messages_processed_total counter",
        "graph_enricher_messages_processed_total 0",
        "# HELP graph_enricher_messages_failed_total Total messages failed",
        "# TYPE graph_enricher_messages_failed_total counter",
        "graph_enricher_messages_failed_total 0",
        "# HELP graph_enricher_nodes_enriched_total Total nodes enriched",
        "# TYPE graph_enricher_nodes_enriched_total counter",
        "graph_enricher_nodes_enriched_total 0",
        "# HELP graph_enricher_dynamic_edges_total Total dynamic edges written",
        "# TYPE graph_enricher_dynamic_edges_total counter",
        "graph_enricher_dynamic_edges_total 0",
        "# HELP graph_enricher_confirmed_edges_total Total static edges confirmed",
        "# TYPE graph_enricher_confirmed_edges_total counter",
        "graph_enricher_confirmed_edges_total 0",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
