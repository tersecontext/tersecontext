# app/main.py
from __future__ import annotations

import logging
import os

import psycopg2
import redis as redis_lib
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from neo4j import GraphDatabase

from .discoverer import run_discover
from .models import DiscoverRequest, DiscoverResponse

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "entrypoint-discoverer"

_neo4j_driver = None
_pg_conn = None
_redis_client = None


def _get_neo4j_driver():
    global _neo4j_driver
    if _neo4j_driver is None:
        uri = os.environ.get("NEO4J_URL", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "localpassword")
        _neo4j_driver = GraphDatabase.driver(uri, auth=(user, password))
    return _neo4j_driver


def _get_pg_conn():
    global _pg_conn
    if _pg_conn is None or _pg_conn.closed:
        dsn = os.environ.get(
            "POSTGRES_DSN",
            "postgres://tersecontext:localpassword@localhost:5432/tersecontext",
        )
        _pg_conn = psycopg2.connect(dsn)
    return _pg_conn


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = redis_lib.from_url(url, decode_responses=False)
    return _redis_client


app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE, "version": VERSION}


@app.get("/ready")
def ready():
    try:
        _get_redis().ping()
        return {"status": "ok"}
    except Exception as exc:
        return JSONResponse(status_code=503, content={"status": "unavailable", "error": str(exc)})


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP entrypoint_discoverer_jobs_queued_total Total EntrypointJobs pushed to Redis",
        "# TYPE entrypoint_discoverer_jobs_queued_total counter",
        "entrypoint_discoverer_jobs_queued_total 0",
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
        return DiscoverResponse(**result)
    except Exception as exc:
        logger.error("Discover failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
