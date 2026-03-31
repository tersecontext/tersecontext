# services/trace-normalizer/app/main.py
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

import redis as redis_lib
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

from .classifier import classify_side_effects
from .emitter import emit_execution_path
from .models import RawTrace
from .normalizer import aggregate_frequencies, compute_percentiles, reconstruct_call_tree
from .reconciler import reconcile

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "trace-normalizer"
INPUT_STREAM = "stream:raw-traces"
CONSUMER_GROUP = "normalizer-group"
CONSUMER_NAME = "normalizer-0"

_redis_client: redis_lib.Redis | None = None
_neo4j_driver = None
_events_processed: int = 0
_consumer_task: asyncio.Task | None = None


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = redis_lib.from_url(url, decode_responses=False)
    return _redis_client


def _get_neo4j_driver():
    global _neo4j_driver
    if _neo4j_driver is None:
        neo4j_url = os.environ.get("NEO4J_URL", "")
        if not neo4j_url:
            return None
        try:
            from neo4j import GraphDatabase
            _neo4j_driver = GraphDatabase.driver(
                neo4j_url,
                auth=(
                    os.environ.get("NEO4J_USER", "neo4j"),
                    os.environ.get("NEO4J_PASSWORD", "localpassword"),
                ),
            )
        except Exception as exc:
            logger.warning("Neo4j driver init failed: %s", exc)
    return _neo4j_driver


def _ensure_consumer_group(r: redis_lib.Redis) -> None:
    try:
        r.xgroup_create(INPUT_STREAM, CONSUMER_GROUP, id="0", mkstream=True)
    except redis_lib.exceptions.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def _process_message(r: redis_lib.Redis, driver, raw_json: str) -> None:
    global _events_processed
    trace = RawTrace.model_validate_json(raw_json)
    nodes = reconstruct_call_tree(trace.events)

    repo = trace.repo or "unknown"
    agg_key = f"trace_agg:{repo}:{trace.entrypoint_stable_id}"
    raw_agg = r.get(agg_key)
    existing_agg = json.loads(raw_agg) if raw_agg else {}
    max_runs = max((v.get("runs", 0) for v in existing_agg.values()), default=0) + 1

    nodes = aggregate_frequencies(r, repo, trace.entrypoint_stable_id, nodes, max_runs)

    side_effects = classify_side_effects(trace.events)

    observed_fns = {ev.fn for ev in trace.events if ev.type == "call"}
    dynamic_only, never_observed = reconcile(driver, repo, trace.entrypoint_stable_id, observed_fns)

    durations = [n.avg_ms for n in nodes if n.avg_ms > 0]
    p50, p99 = compute_percentiles(durations or [trace.duration_ms])

    from .models import ExecutionPath
    ep = ExecutionPath(
        entrypoint_stable_id=trace.entrypoint_stable_id,
        commit_sha=trace.commit_sha,
        call_sequence=nodes,
        side_effects=side_effects,
        dynamic_only_edges=dynamic_only,
        never_observed_static_edges=never_observed,
        timing_p50_ms=p50,
        timing_p99_ms=p99,
    )
    emit_execution_path(r, ep)
    _events_processed += 1


async def _consumer_loop(r: redis_lib.Redis, driver) -> None:
    _ensure_consumer_group(r)
    while True:
        try:
            messages = r.xreadgroup(
                CONSUMER_GROUP,
                CONSUMER_NAME,
                {INPUT_STREAM: ">"},
                count=10,
                block=1000,
            )
            if not messages:
                await asyncio.sleep(0)
                continue
            for _stream, entries in messages:
                for msg_id, fields in entries:
                    raw = fields.get(b"event") or fields.get("event")
                    if raw:
                        try:
                            _process_message(r, driver, raw if isinstance(raw, str) else raw.decode())
                            r.xack(INPUT_STREAM, CONSUMER_GROUP, msg_id)
                        except Exception as exc:
                            logger.error("Failed to process message %s: %s", msg_id, exc)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Consumer loop error: %s", exc)
            await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _consumer_task
    r = _get_redis()
    driver = _get_neo4j_driver()
    _consumer_task = asyncio.create_task(_consumer_loop(r, driver))
    try:
        yield
    finally:
        if _consumer_task:
            _consumer_task.cancel()
            try:
                await _consumer_task
            except asyncio.CancelledError:
                pass
        if _redis_client is not None:
            _redis_client.close()
        if _neo4j_driver is not None:
            _neo4j_driver.close()


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
        return JSONResponse(status_code=503, content={"status": "unavailable", "error": str(exc)})


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP trace_normalizer_events_processed_total Total ExecutionPath events emitted",
        "# TYPE trace_normalizer_events_processed_total counter",
        f"trace_normalizer_events_processed_total {_events_processed}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
