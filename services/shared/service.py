from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class ServiceBase:
    """Shared FastAPI scaffolding for all Python microservices.

    Provides an APIRouter with /health and /ready (with pluggable dep
    checkers), and a singleton Redis client. Each service creates its own
    FastAPI app, includes svc.router, and keeps its own /metrics, lifespan,
    and business-logic routes.

    Usage::

        svc = ServiceBase("my-service", "0.1.0")
        svc.add_dep_checker(my_redis_checker)

        app = FastAPI(lifespan=lifespan)
        app.include_router(svc.router)   # registers /health and /ready

        @app.get("/metrics")
        def metrics(): ...
    """

    def __init__(self, name: str, version: str = "0.1.0") -> None:
        self.name = name
        self.version = version
        self._redis: aioredis.Redis | None = None
        self._dep_checkers: list[Callable[[], Awaitable[str | None]]] = []

        self.router = APIRouter()

        @self.router.get("/health", name=f"{name}-health")
        def health() -> dict[str, str]:
            return {"status": "ok", "service": name, "version": version}

        @self.router.get("/ready", name=f"{name}-ready")
        async def ready() -> Any:
            errors: list[str] = []
            for checker in self._dep_checkers:
                err = await checker()
                if err:
                    errors.append(err)
            if errors:
                return JSONResponse(
                    status_code=503,
                    content={"status": "unavailable", "errors": errors},
                )
            return {"status": "ok"}

    def add_dep_checker(self, checker: Callable[[], Awaitable[str | None]]) -> None:
        """Register a dep checker. Return None if OK, an error string if not."""
        self._dep_checkers.append(checker)

    def get_redis(self) -> aioredis.Redis:
        """Return the singleton Redis client, creating it on first call using REDIS_URL."""
        if self._redis is None:
            self._redis = aioredis.from_url(
                os.environ.get("REDIS_URL", "redis://localhost:6379")
            )
        return self._redis

    async def close_redis(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
