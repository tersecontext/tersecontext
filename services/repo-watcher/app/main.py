# services/repo-watcher/app/main.py
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis as redis_lib
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from .models import HookRequest, IndexRequest
from .watcher import _get_last_sha, _repo_name, _set_last_sha, process_commit, run_watcher

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "repo-watcher"

_redis_client: redis_lib.Redis | None = None
_watcher_task: asyncio.Task | None = None


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = redis_lib.from_url(url, decode_responses=False)
    return _redis_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _watcher_task
    watch_mode = os.environ.get("WATCH_MODE", "hook")
    if watch_mode == "poll":
        repo_root = os.environ.get("REPO_ROOT", "/repos")
        poll_interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "30"))
        _watcher_task = asyncio.create_task(
            run_watcher(repo_root, _get_redis(), poll_interval)
        )
    try:
        yield
    finally:
        if _watcher_task:
            _watcher_task.cancel()
            try:
                await _watcher_task
            except asyncio.CancelledError:
                pass
        if _redis_client is not None:
            _redis_client.close()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE, "version": VERSION}


@app.get("/ready")
def ready():
    try:
        _get_redis().ping()
        return {"status": "ok"}
    except Exception as exc:
        return JSONResponse(status_code=503, content={"status": "unavailable", "error": "redis unavailable"})


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP repo_watcher_events_emitted_total Total FileChanged events emitted",
        "# TYPE repo_watcher_events_emitted_total counter",
        "repo_watcher_events_emitted_total 0",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")


@app.post("/hook", status_code=202)
def hook(req: HookRequest):
    r = _get_redis()
    repo = _repo_name(req.repo_path)
    prev_sha = _get_last_sha(r, repo)
    try:
        events, files = process_commit(r, req.repo_path, prev_sha, req.commit_sha)
        _set_last_sha(r, repo, req.commit_sha)
        return {"events_emitted": events, "files_changed": files}
    except Exception as exc:
        logger.error("Hook processing failed: %s", exc)
        raise HTTPException(status_code=500, detail="Hook processing failed")


@app.post("/index", status_code=202)
def index(req: IndexRequest):
    r = _get_redis()
    repo = _repo_name(req.repo_path)
    prev_sha = None if req.full_rescan else _get_last_sha(r, repo)

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=req.repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail="Cannot read HEAD of repo")

    current_sha = result.stdout.strip()
    try:
        process_commit(r, req.repo_path, prev_sha, current_sha)
        _set_last_sha(r, repo, current_sha)
        return {"queued": True}
    except Exception as exc:
        logger.error("Index processing failed: %s", exc)
        raise HTTPException(status_code=500, detail="Index processing failed")


@app.get("/status")
def status(repo_path: str = ""):
    """
    Return indexing status for a repo.
    Pass ?repo_path=/path/to/repo or defaults to REPO_ROOT env var.
    """
    r = _get_redis()
    if not repo_path:
        repo_path = os.environ.get("REPO_ROOT", "/repos")
    from .models import _validate_repo_path
    try:
        repo_path = _validate_repo_path(repo_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    repo = _repo_name(repo_path)
    last_sha = _get_last_sha(r, repo) or ""
    return {
        "repo": repo,
        "last_sha": last_sha,
        "last_indexed_at": datetime.now(timezone.utc).isoformat(),
        "pending": False,
    }

