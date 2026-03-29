import asyncio
import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "embedder"

_redis_client: aioredis.Redis | None = None
_neo4j_client = None
_provider = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = aioredis.from_url(url)
    return _redis_client


def _make_provider():
    from .providers.ollama import OllamaProvider
    from .providers.voyage import VoyageProvider

    provider_name = os.environ.get("EMBEDDING_PROVIDER", "ollama")
    if provider_name == "voyage":
        return VoyageProvider()
    return OllamaProvider()


def _get_embedding_dim() -> int:
    provider_name = os.environ.get("EMBEDDING_PROVIDER", "ollama")
    default = "768" if provider_name == "ollama" else "1024"
    return int(os.environ.get("EMBEDDING_DIM", default))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _neo4j_client, _provider

    from .neo4j_client import Neo4jClient

    _provider = _make_provider()
    _neo4j_client = Neo4jClient()

    batch_size = int(os.environ.get("BATCH_SIZE", "64"))
    embedding_dim = _get_embedding_dim()

    from .consumer import run_consumer

    task = asyncio.create_task(
        run_consumer(_provider, _neo4j_client, batch_size, embedding_dim)
    )
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if _neo4j_client:
            _neo4j_client.close()


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
    try:
        if _neo4j_client:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _neo4j_client.verify_connectivity)
    except Exception as exc:
        errors.append(f"neo4j: {exc}")
    if errors:
        return JSONResponse(status_code=503, content={"status": "unavailable", "errors": errors})
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP embedder_messages_processed_total Total messages processed",
        "# TYPE embedder_messages_processed_total counter",
        "embedder_messages_processed_total 0",
        "# HELP embedder_messages_failed_total Total messages failed",
        "# TYPE embedder_messages_failed_total counter",
        "embedder_messages_failed_total 0",
        "# HELP embedder_nodes_embedded_total Total nodes embedded",
        "# TYPE embedder_nodes_embedded_total counter",
        "embedder_nodes_embedded_total 0",
        "# HELP embedder_nodes_skipped_total Total nodes skipped (unchanged)",
        "# TYPE embedder_nodes_skipped_total counter",
        "embedder_nodes_skipped_total 0",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
