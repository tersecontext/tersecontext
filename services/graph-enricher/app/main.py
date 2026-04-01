# services/graph-enricher/app/main.py
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from neo4j import GraphDatabase
from shared.service import ServiceBase, validate_env

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
_svc = ServiceBase("graph-enricher", VERSION)
_driver = None
_consumer: "GraphEnricherConsumer | None" = None


def _make_driver():
    return GraphDatabase.driver(
        os.environ.get("NEO4J_URL", "bolt://localhost:7687"),
        auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]),
    )


async def _check_neo4j() -> str | None:
    if _driver is None:
        return "neo4j: driver not initialized"
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _driver.verify_connectivity)
        return None
    except Exception as exc:
        return f"neo4j: {exc}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _driver, _consumer
    from .consumer import GraphEnricherConsumer

    validate_env(["NEO4J_PASSWORD"], "graph-enricher")
    _svc._dep_checkers.clear()   # idempotent restart safety
    _driver = _make_driver()
    consumer = GraphEnricherConsumer(_driver)
    _consumer = consumer
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")

    async def check_redis() -> str | None:
        try:
            await _svc.get_redis().ping()
            return None
        except Exception as exc:
            return f"redis: {exc}"

    _svc.add_dep_checker(check_redis)
    _svc.add_dep_checker(_check_neo4j)

    task = asyncio.create_task(consumer.run(redis_url))
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
        await _svc.close_redis()


app = FastAPI(lifespan=lifespan)
app.include_router(_svc.router)  # registers /health and /ready


@app.get("/metrics")
def metrics():
    processed = _consumer.messages_processed if _consumer else 0
    failed = _consumer.batches_failed if _consumer else 0
    enriched = _consumer.nodes_enriched if _consumer else 0
    dynamic = _consumer.dynamic_edges_written if _consumer else 0
    confirmed = _consumer.confirmed_edges_written if _consumer else 0
    lines = [
        "# HELP graph_enricher_messages_processed_total Total messages processed",
        "# TYPE graph_enricher_messages_processed_total counter",
        f"graph_enricher_messages_processed_total {processed}",
        "# HELP graph_enricher_messages_failed_total Total messages failed",
        "# TYPE graph_enricher_messages_failed_total counter",
        f"graph_enricher_messages_failed_total {failed}",
        "# HELP graph_enricher_nodes_enriched_total Total nodes enriched",
        "# TYPE graph_enricher_nodes_enriched_total counter",
        f"graph_enricher_nodes_enriched_total {enriched}",
        "# HELP graph_enricher_dynamic_edges_total Total dynamic edges written",
        "# TYPE graph_enricher_dynamic_edges_total counter",
        f"graph_enricher_dynamic_edges_total {dynamic}",
        "# HELP graph_enricher_confirmed_edges_total Total static edges confirmed",
        "# TYPE graph_enricher_confirmed_edges_total counter",
        f"graph_enricher_confirmed_edges_total {confirmed}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
