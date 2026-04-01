from __future__ import annotations

import logging
import os
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def validate_env(required: list[str], service: str) -> None:
    """Exit immediately if any required env var is missing or empty.

    Treats empty string as missing — an empty NEO4J_PASSWORD is not valid.
    Collects all missing vars before exiting so the user sees everything at once.
    """
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        lines = [f"[{service}] Missing required environment variables:"]
        for k in missing:
            lines.append(f"  - {k}")
        lines.append("See usage.md for configuration details.")
        print("\n".join(lines), file=sys.stderr)
        sys.exit(1)


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
        # Each entry: (checker_fn, dep_name, required)
        self._dep_checkers: list[tuple[Callable[[], Awaitable[str | None]], str, bool]] = []

        self.router = APIRouter()

        @self.router.get("/health", name=f"{name}-health")
        def health() -> dict[str, str]:
            return {"status": "ok", "service": name, "version": version}

        @self.router.get("/ready", name=f"{name}-ready")
        async def ready() -> Any:
            dep_results: dict[str, dict] = {}
            any_required_failed = False
            any_optional_failed = False

            for checker, dep_name, required in self._dep_checkers:
                err = await checker()
                if err:
                    dep_results[dep_name] = {"status": "error", "error": err, "required": required}
                    if required:
                        any_required_failed = True
                    else:
                        any_optional_failed = True
                else:
                    dep_results[dep_name] = {"status": "ok", "required": required}

            if any_required_failed:
                return JSONResponse(
                    status_code=503,
                    content={"status": "unavailable", "deps": dep_results},
                )
            if any_optional_failed:
                return {"status": "degraded", "deps": dep_results}
            return {"status": "ok", "deps": dep_results}

    def add_dep_checker(
        self,
        checker: Callable[[], Awaitable[str | None]],
        name: str | None = None,
        required: bool = True,
    ) -> None:
        """Register a dep checker. Returns None if OK, an error string if not.

        name defaults to checker.__name__. required=False means failure returns
        HTTP 200 with status 'degraded' instead of 503 'unavailable'.
        """
        dep_name = name if name is not None else checker.__name__
        self._dep_checkers.append((checker, dep_name, required))

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
