# app/main.py
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import psycopg2
import redis as redis_lib
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from neo4j import GraphDatabase
from shared.service import ServiceBase, validate_env

from .discoverer import run_discover
from .models import DiscoverRequest, DiscoverResponse

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
_svc = ServiceBase("entrypoint-discoverer", VERSION)

_neo4j_driver = None
_pg_conn = None
_redis_client = None
_jobs_queued_total: int = 0


def _get_neo4j_driver():
    global _neo4j_driver
    if _neo4j_driver is None:
        uri = os.environ.get("NEO4J_URL", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ["NEO4J_PASSWORD"]
        _neo4j_driver = GraphDatabase.driver(uri, auth=(user, password))
    return _neo4j_driver


def _get_pg_conn():
    global _pg_conn
    if _pg_conn is None or _pg_conn.closed:
        dsn = os.environ["POSTGRES_DSN"]
        _pg_conn = psycopg2.connect(dsn)
    return _pg_conn


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = redis_lib.from_url(url, decode_responses=False)
    return _redis_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_env(["NEO4J_PASSWORD", "POSTGRES_DSN"], "entrypoint-discoverer")
    _svc._dep_checkers.clear()  # idempotent restart safety

    async def check_redis() -> str | None:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _get_redis().ping)
            return None
        except Exception as exc:
            return f"redis: {exc}"

    _svc.add_dep_checker(check_redis)
    try:
        yield
    finally:
        global _redis_client
        if _redis_client is not None:
            _redis_client.close()
            _redis_client = None


app = FastAPI(lifespan=lifespan)
app.include_router(_svc.router)


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP entrypoint_discoverer_jobs_queued_total Total EntrypointJobs pushed to Redis",
        "# TYPE entrypoint_discoverer_jobs_queued_total counter",
        f"entrypoint_discoverer_jobs_queued_total {_jobs_queued_total}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")


@app.post("/discover", status_code=202, response_model=DiscoverResponse)
def discover(req: DiscoverRequest):
    try:
        result = run_discover(
            neo4j_driver=_get_neo4j_driver(),
            pg_conn=_get_pg_conn(),
            redis_client=_get_redis(),
            repo=req.repo,
            trigger=req.trigger,
        )
        global _jobs_queued_total
        _jobs_queued_total += result["queued"]
        return DiscoverResponse(**result)
    except Exception as exc:
        logger.error("Discover failed: %s", exc)
        raise HTTPException(status_code=500, detail="Discovery failed")
