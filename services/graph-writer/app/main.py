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
SERVICE = "graph-writer"

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
    password = os.environ["NEO4J_PASSWORD"]
    return GraphDatabase.driver(url, auth=(user, password))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _driver
    from .consumer import run_edge_consumer, run_node_consumer

    _driver = _make_driver()
    edge_task = asyncio.create_task(run_edge_consumer(_driver))
    node_task = asyncio.create_task(run_node_consumer(_driver))
    try:
        yield
    finally:
        edge_task.cancel()
        node_task.cancel()
        for task in (edge_task, node_task):
            try:
                await task
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
        "# HELP graph_writer_messages_processed_total Total messages processed",
        "# TYPE graph_writer_messages_processed_total counter",
        "graph_writer_messages_processed_total 0",
        "# HELP graph_writer_messages_failed_total Total messages failed",
        "# TYPE graph_writer_messages_failed_total counter",
        "graph_writer_messages_failed_total 0",
        "# HELP graph_writer_nodes_written_total Total nodes written",
        "# TYPE graph_writer_nodes_written_total counter",
        "graph_writer_nodes_written_total 0",
        "# HELP graph_writer_edges_written_total Total edges passed to Neo4j",
        "# TYPE graph_writer_edges_written_total counter",
        "graph_writer_edges_written_total 0",
        "# HELP graph_writer_tombstones_written_total Total nodes tombstoned",
        "# TYPE graph_writer_tombstones_written_total counter",
        "graph_writer_tombstones_written_total 0",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
