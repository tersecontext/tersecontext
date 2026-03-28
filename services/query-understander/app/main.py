import asyncio
import logging
import os
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from .cache import cache_get, cache_set
from .models import QueryIntent, UnderstandRequest
from .understander import understand

logger = logging.getLogger(__name__)


async def _check_ollama(base_url: str) -> bool:
    delays = [1, 2, 4, 8, 16]
    for i, delay in enumerate(delays):
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{base_url}/api/tags")
                if resp.status_code == 200:
                    return True
        except Exception:
            pass
        if i < len(delays) - 1:
            await asyncio.sleep(delay)
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    llm_base_url = os.getenv("LLM_BASE_URL", "http://ollama:11434")

    app.state.redis = aioredis.from_url(redis_url, decode_responses=True)
    try:
        app.state.ollama_ready = await _check_ollama(llm_base_url)

        if not app.state.ollama_ready:
            logger.warning("Ollama unreachable at startup — running in fallback mode")

        yield
    finally:
        await app.state.redis.aclose()


app = FastAPI(title="query-understander", version="0.1.0", lifespan=lifespan)
Instrumentator().instrument(app).expose(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "query-understander", "version": "0.1.0"}


@app.get("/ready")
async def ready(request: Request):
    if request.app.state.ollama_ready:
        return {"status": "ready"}
    return JSONResponse(
        status_code=503,
        content={"status": "unavailable", "reason": "ollama unreachable"},
    )


@app.post("/understand", response_model=QueryIntent)
async def understand_endpoint(req: UnderstandRequest, request: Request):
    redis_client = request.app.state.redis

    cached = await cache_get(redis_client, req.question, req.repo)
    if cached:
        logger.info("Cache hit for question: %r", req.question)
        return cached

    intent, from_ollama = await understand(req.question)

    if from_ollama:
        await cache_set(redis_client, req.question, req.repo, intent)

    return intent
