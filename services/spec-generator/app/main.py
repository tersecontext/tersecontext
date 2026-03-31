import asyncio
import logging
import os
from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "spec-generator"

_redis_client: aioredis.Redis | None = None
_store = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = aioredis.from_url(url)
    return _redis_client


def _make_provider():
    from app.providers.ollama import OllamaProvider
    from app.providers.voyage import VoyageProvider

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
    global _store

    from app.store import SpecStore
    from app.consumer import run_consumer

    provider = _make_provider()
    postgres_dsn = os.environ.get("POSTGRES_DSN", "postgres://tersecontext:localpassword@localhost:5432/tersecontext")
    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    embedding_dim = _get_embedding_dim()

    _store = SpecStore(postgres_dsn, qdrant_url, provider, embedding_dim)
    await _store.ensure_schema()
    await _store.ensure_collection()

    task = asyncio.create_task(run_consumer(_store))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await _store.close()


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
        if _store:
            pool = await _store._get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
    except Exception as exc:
        errors.append(f"postgres: {exc}")
    try:
        if _store:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _store._qdrant.get_collections)
    except Exception as exc:
        errors.append(f"qdrant: {exc}")
    if errors:
        return JSONResponse(status_code=503, content={"status": "unavailable", "errors": errors})
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP spec_generator_messages_processed_total Total messages processed",
        "# TYPE spec_generator_messages_processed_total counter",
        "spec_generator_messages_processed_total 0",
        "# HELP spec_generator_messages_failed_total Total messages failed",
        "# TYPE spec_generator_messages_failed_total counter",
        "spec_generator_messages_failed_total 0",
        "# HELP spec_generator_specs_written_total Total specs written to Postgres",
        "# TYPE spec_generator_specs_written_total counter",
        "spec_generator_specs_written_total 0",
        "# HELP spec_generator_specs_embedded_total Total specs embedded to Qdrant",
        "# TYPE spec_generator_specs_embedded_total counter",
        "spec_generator_specs_embedded_total 0",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
