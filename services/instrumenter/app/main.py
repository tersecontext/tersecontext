# services/instrumenter/app/main.py
from __future__ import annotations

import logging
import os
import uuid

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

from .config import PATCH_CATALOG
from .models import InstrumentRequest, InstrumentResponse, PatchSpec

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "instrumenter"

_sessions_created: int = 0

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE, "version": VERSION}


@app.get("/ready")
def ready():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP instrumenter_sessions_created_total Total instrumentation sessions created",
        "# TYPE instrumenter_sessions_created_total counter",
        f"instrumenter_sessions_created_total {_sessions_created}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")


@app.post("/instrument")
def instrument(req: InstrumentRequest) -> InstrumentResponse:
    global _sessions_created

    if not req.stable_id or not req.file_path or not req.repo:
        return JSONResponse(
            status_code=400,
            content={"error": "stable_id, file_path, and repo are required"},
        )

    session_id = str(uuid.uuid4())
    tempdir_prefix = os.environ.get("TEMPDIR_PREFIX", "/tmp/tc")
    try:
        timeout_ms = int(os.environ.get("TIMEOUT_MS", "30000"))
    except ValueError:
        timeout_ms = 30000

    _sessions_created += 1

    return InstrumentResponse(
        session_id=session_id,
        status="ready",
        patches=[PatchSpec(**entry) for entry in PATCH_CATALOG],
        output_key=f"trace_events:{session_id}",
        tempdir=f"{tempdir_prefix}/{session_id}",
        timeout_ms=timeout_ms,
    )
