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
