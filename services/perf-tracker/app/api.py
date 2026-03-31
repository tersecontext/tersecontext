from __future__ import annotations
import json
import os
from typing import Optional

from fastapi import APIRouter, Query

from app.analyzer import compute_trend_slope, detect_slow_functions
from app.store import PerfStore

router = APIRouter(prefix="/api/v1")


def _parse_window(window: str) -> int:
    window = window.strip().lower()
    if window.endswith("d"):
        return int(window[:-1]) * 24
    if window.endswith("h"):
        return int(window[:-1])
    return 24


def _get_store() -> PerfStore:
    import app.main as main_mod
    return main_mod._store


@router.get("/realtime/{repo}")
async def get_realtime(repo: str):
    store = _get_store()
    return await store.get_realtime(repo)


@router.get("/bottlenecks/{repo}")
async def get_bottlenecks(
    repo: str,
    category: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
):
    store = _get_store()
    raw = await store.get_bottlenecks_json(repo)
    items = [json.loads(b) for b in raw]
    if category:
        items = [b for b in items if b.get("category") == category]
    if severity:
        items = [b for b in items if b.get("severity") == severity]
    return items


@router.get("/slow-functions/{repo}")
async def get_slow_functions_endpoint(
    repo: str,
    limit: int = Query(20, ge=1, le=100),
    commit_sha: Optional[str] = Query(None),
):
    store = _get_store()
    return await store.get_slow_functions(repo, limit=limit, commit_sha=commit_sha)


@router.get("/hot-paths/{repo}")
async def get_hot_paths(repo: str, limit: int = Query(20, ge=1, le=100)):
    store = _get_store()
    results = await store.get_hot_functions(repo, limit=limit)
    return [{"entity_id": name, "cumulative_ms": score} for name, score in results]


@router.get("/regressions/{repo}")
async def get_regressions(
    repo: str,
    base_sha: str = Query(...),
    head_sha: str = Query(...),
):
    store = _get_store()
    threshold = float(os.environ.get("REGRESSION_THRESHOLD_PCT", "20"))
    return await store.get_regressions(repo, base_sha, head_sha, threshold)


@router.get("/trends/{repo}/{entity_id:path}")
async def get_trends(
    repo: str,
    entity_id: str,
    metric_name: str = Query("fn_duration"),
    window: str = Query("24h"),
):
    store = _get_store()
    hours = _parse_window(window)
    points = await store.get_trends(repo, entity_id, metric_name, window_hours=hours)
    slope, direction = compute_trend_slope(points)
    serialized = [
        {"value": p["value"], "recorded_at": p["recorded_at"].isoformat() if hasattr(p["recorded_at"], "isoformat") else str(p["recorded_at"])}
        for p in points
    ]
    return {
        "entity_id": entity_id,
        "metric_name": metric_name,
        "window": window,
        "points": serialized,
        "trend": {"slope": slope, "direction": direction},
    }


@router.get("/summary/{repo}")
async def get_summary(repo: str):
    store = _get_store()
    realtime = await store.get_realtime(repo)
    slow = await store.get_slow_functions(repo, limit=5)
    bottlenecks_raw = await store.get_bottlenecks_json(repo)
    bottlenecks = [json.loads(b) for b in bottlenecks_raw]
    counts = await store.get_summary_counts(repo)
    return {
        "repo": repo,
        "pipeline_health": realtime,
        "top_slow_functions": slow,
        "active_bottlenecks": bottlenecks,
        "metric_counts": counts,
    }


@router.post("/analyze/{repo}")
async def analyze(repo: str):
    store = _get_store()
    slow = await store.get_slow_functions(repo, limit=20)
    realtime = await store.get_realtime(repo)
    bottlenecks_raw = await store.get_bottlenecks_json(repo)
    bottlenecks = [json.loads(b) for b in bottlenecks_raw]
    counts = await store.get_summary_counts(repo)
    slow_bottlenecks = detect_slow_functions(slow, repo=repo)
    return {
        "repo": repo,
        "pipeline_health": realtime,
        "bottlenecks": bottlenecks + [b.model_dump(mode="json") for b in slow_bottlenecks],
        "slow_functions": slow,
        "metric_counts": counts,
    }
