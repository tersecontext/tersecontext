from __future__ import annotations
import asyncio
import logging
import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from neo4j import GraphDatabase

from .consumer import run_consumer
from .languages import parse_language_servers

logging.basicConfig(level=logging.INFO)
app = FastAPI()

_neo4j_driver = None
_language_servers: dict = {}


@app.on_event("startup")
async def startup():
    global _neo4j_driver, _language_servers

    neo4j_url = os.environ.get("NEO4J_URL", "bolt://neo4j:7687")
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "")
    _neo4j_driver = GraphDatabase.driver(neo4j_url, auth=(neo4j_user, neo4j_password))

    _language_servers = parse_language_servers(os.environ.get("LANGUAGE_SERVERS", ""))

    asyncio.create_task(run_consumer(_language_servers, _neo4j_driver))


@app.on_event("shutdown")
async def shutdown():
    if _neo4j_driver:
        _neo4j_driver.close()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    if _neo4j_driver is None:
        return JSONResponse(status_code=503, content={"status": "not ready"})
    return {"status": "ready"}


@app.get("/metrics")
def metrics():
    return JSONResponse(status_code=200, content={})


@app.post("/index")
async def index(body: dict):
    repo = body.get("repo")
    path = body.get("path")
    if not repo or not path:
        return JSONResponse(status_code=400, content={"error": "repo and path required"})
    from .indexer import index_repo
    edges = await index_repo(repo, path, _language_servers, _neo4j_driver)
    return {"status": "indexed", "edges_written": edges}
