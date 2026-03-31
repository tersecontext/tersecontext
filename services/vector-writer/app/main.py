from __future__ import annotations
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "vector-writer"

_redis_client: aioredis.Redis | None = None
_writer = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = aioredis.from_url(url)
    return _redis_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _writer
    from .writer import QdrantWriter
    from .consumer import run_embedded_consumer, run_delete_consumer

    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    embedding_dim = int(os.environ.get("EMBEDDING_DIM", "768"))

    _writer = QdrantWriter(url=qdrant_url, embedding_dim=embedding_dim)
    await _writer.ensure_collection()

    task1 = asyncio.create_task(run_embedded_consumer(_writer))
    task2 = asyncio.create_task(run_delete_consumer(_writer))
    try:
        yield
    finally:
        task1.cancel()
        task2.cancel()
        for task in [task1, task2]:
            try:
                await task
            except asyncio.CancelledError:
                pass


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
        if _writer:
            await _writer.get_collections()
    except Exception as exc:
        errors.append(f"qdrant: {exc}")
    if errors:
        return JSONResponse(status_code=503, content={"status": "unavailable", "errors": errors})
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP vector_writer_messages_processed_total Total messages processed",
        "# TYPE vector_writer_messages_processed_total counter",
        "vector_writer_messages_processed_total 0",
        "# HELP vector_writer_messages_failed_total Total messages failed",
        "# TYPE vector_writer_messages_failed_total counter",
        "vector_writer_messages_failed_total 0",
        "# HELP vector_writer_points_upserted_total Total points upserted to Qdrant",
        "# TYPE vector_writer_points_upserted_total counter",
        "vector_writer_points_upserted_total 0",
        "# HELP vector_writer_points_deleted_total Total points deleted from Qdrant",
        "# TYPE vector_writer_points_deleted_total counter",
        "vector_writer_points_deleted_total 0",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
