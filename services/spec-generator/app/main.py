# services/spec-generator/app/main.py
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from shared.service import ServiceBase

from . import consumer as _consumer

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
_svc = ServiceBase("spec-generator", VERSION)
_store = None


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
    from app.consumer import SpecGeneratorConsumer

    _svc._dep_checkers.clear()  # idempotent restart safety

    provider = _make_provider()
    postgres_dsn = os.environ["POSTGRES_DSN"]
    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    embedding_dim = _get_embedding_dim()

    _store = SpecStore(postgres_dsn, qdrant_url, provider, embedding_dim)
    await _store.ensure_schema()
    await _store.ensure_collection()

    async def check_redis() -> str | None:
        try:
            await _svc.get_redis().ping()
            return None
        except Exception as exc:
            return f"redis: {exc}"

    async def check_postgres() -> str | None:
        try:
            if _store:
                pool = await _store._get_pool()
                async with pool.acquire() as conn:
                    await conn.fetchval("SELECT 1")
            return None
        except Exception as exc:
            return f"postgres: {exc}"

    async def check_qdrant() -> str | None:
        try:
            if _store:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _store._qdrant.get_collections)
            return None
        except Exception as exc:
            return f"qdrant: {exc}"

    _svc.add_dep_checker(check_redis)
    _svc.add_dep_checker(check_postgres)
    _svc.add_dep_checker(check_qdrant)

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    consumer = SpecGeneratorConsumer(_store)
    task = asyncio.create_task(consumer.run(redis_url))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await _store.close()
        await _svc.close_redis()


app = FastAPI(lifespan=lifespan)
app.include_router(_svc.router)  # registers /health and /ready


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP spec_generator_messages_processed_total Total messages processed",
        "# TYPE spec_generator_messages_processed_total counter",
        f"spec_generator_messages_processed_total {_consumer.messages_processed_total}",
        "# HELP spec_generator_specs_written_total Total specs written to Postgres",
        "# TYPE spec_generator_specs_written_total counter",
        f"spec_generator_specs_written_total {_consumer.specs_written_total}",
        "# HELP spec_generator_specs_embedded_total Total specs embedded to Qdrant",
        "# TYPE spec_generator_specs_embedded_total counter",
        f"spec_generator_specs_embedded_total {_consumer.specs_embedded_total}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
