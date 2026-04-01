# services/instrumenter/app/main.py
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import logging
import os
import shutil
import signal
import sys
import tempfile
import time
import uuid
from contextlib import ExitStack, asynccontextmanager
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from shared.service import ServiceBase

from .config import DEFAULT_CAPTURE_ARGS, PATCH_CATALOG
from .mocking import create_mock_patches
from .models import InstrumentRequest, InstrumentResponse, PatchSpec
from .trace import (
    TraceSession,
    create_trace_func,
    install_async_hooks,
    uninstall_async_hooks,
)

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
_svc = ServiceBase("instrumenter", VERSION)

MAX_SESSIONS = 100

_sessions_created: int = 0
_runs_completed: int = 0
_sessions: dict[str, TraceSession] = {}
_session_created_at: dict[str, float] = {}


class RunRequest(BaseModel):
    session_id: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    _svc._dep_checkers.clear()  # idempotent restart safety

    async def _evict():
        while True:
            await asyncio.sleep(30)
            now = time.monotonic()
            expired = [
                sid
                for sid, created in _session_created_at.items()
                if now - created > 60
            ]
            for sid in expired:
                session = _sessions.pop(sid, None)
                _session_created_at.pop(sid, None)
                if session:
                    shutil.rmtree(session.tempdir, ignore_errors=True)
                logger.info("Evicted stale session %s", sid)

    eviction_task = asyncio.create_task(_evict())
    try:
        yield
    finally:
        eviction_task.cancel()
        try:
            await eviction_task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)
app.include_router(_svc.router)


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP instrumenter_sessions_created_total Total instrumentation sessions created",
        "# TYPE instrumenter_sessions_created_total counter",
        f"instrumenter_sessions_created_total {_sessions_created}",
        "# HELP instrumenter_runs_completed_total Total trace runs completed",
        "# TYPE instrumenter_runs_completed_total counter",
        f"instrumenter_runs_completed_total {_runs_completed}",
        "# HELP instrumenter_active_sessions Current active sessions",
        "# TYPE instrumenter_active_sessions gauge",
        f"instrumenter_active_sessions {len(_sessions)}",
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

    if len(_sessions) >= MAX_SESSIONS:
        return JSONResponse(
            status_code=503,
            content={"error": "Max concurrent sessions reached"},
        )

    session_id = str(uuid.uuid4())
    tempdir_prefix = os.environ.get("TEMPDIR_PREFIX", "/tmp/tc")
    try:
        timeout_ms = int(os.environ.get("TIMEOUT_MS", "30000"))
    except ValueError:
        timeout_ms = 30000

    td = tempfile.mkdtemp(prefix=f"tc_{session_id}_")

    coverage_filter_set = set(req.coverage_filter) if req.coverage_filter else None
    effective_capture_args = req.capture_args if req.capture_args else DEFAULT_CAPTURE_ARGS

    session = TraceSession(
        session_id=session_id,
        file_path=req.file_path,
        repo=req.repo,
        stable_id=req.stable_id,
        tempdir=td,
        capture_args=effective_capture_args,
        coverage_filter=coverage_filter_set,
    )
    _sessions[session_id] = session
    _session_created_at[session_id] = time.monotonic()

    _sessions_created += 1

    return InstrumentResponse(
        session_id=session_id,
        status="ready",
        patches=[PatchSpec(**entry) for entry in PATCH_CATALOG],
        output_key=f"trace_events:{session_id}",
        tempdir=f"{tempdir_prefix}/{session_id}",
        timeout_ms=timeout_ms,
    )


def _load_entrypoint(session: TraceSession):
    """Load the target module/function. Returns (callable, is_coroutine)."""
    if session.file_path == "__test_stub__":
        return (lambda: None), False
    spec = importlib.util.spec_from_file_location("_target", session.file_path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        entry = getattr(mod, "main", None) or next(
            (v for k, v in vars(mod).items() if k.startswith("test_") and callable(v)),
            None,
        )
        if entry and inspect.iscoroutinefunction(entry):
            return entry, True
        if entry:
            return entry, False
    return (lambda: None), False


def _execute_sync(fn, timeout_s: int = 30) -> None:
    import threading

    if threading.current_thread() is threading.main_thread():
        def _timeout_handler(signum, frame):
            raise TimeoutError(f"Execution exceeded {timeout_s}s")

        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout_s)
        try:
            fn()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
    else:
        # signal.alarm only works in the main thread; fall back to no timeout
        fn()


def _execute_async(fn, timeout_s: int = 30) -> None:
    async def _run():
        await asyncio.wait_for(fn(), timeout=timeout_s)

    asyncio.run(_run())


@app.post("/run")
def run(req: RunRequest):
    global _runs_completed

    session = _sessions.get(req.session_id)
    if session is None:
        return JSONResponse(status_code=404, content={"error": "Session not found"})

    io_events: list = []
    mock_patches = create_mock_patches(io_events, session.tempdir)
    entry_fn, is_async = _load_entrypoint(session)
    trace_func = create_trace_func(session)

    start = time.monotonic()
    with ExitStack() as stack:
        for p in mock_patches:
            try:
                stack.enter_context(patch(p["target"], side_effect=p["side_effect"]))
            except Exception:
                pass  # Target may not be importable
        sys.settrace(trace_func)
        install_async_hooks(session)
        try:
            if is_async:
                _execute_async(entry_fn)
            else:
                _execute_sync(entry_fn)
        finally:
            sys.settrace(None)
            uninstall_async_hooks()

    duration_ms = (time.monotonic() - start) * 1000.0
    events = [e.model_dump() for e in session.events]

    # Clean up session and its tempdir
    shutil.rmtree(session.tempdir, ignore_errors=True)
    _sessions.pop(req.session_id, None)
    _session_created_at.pop(req.session_id, None)
    _runs_completed += 1

    return {"events": events, "duration_ms": round(duration_ms, 3)}
