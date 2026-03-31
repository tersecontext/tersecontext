from __future__ import annotations
import asyncio
import json
import logging
import os
import socket
import time

import redis.asyncio as aioredis
import redis.exceptions
from pydantic import ValidationError

from app.models import PerfMetric
from app.analyzer import detect_queue_buildup, detect_processing_lag, detect_slow_functions, detect_throughput_drop
from app.store import PerfStore

logger = logging.getLogger(__name__)

STREAM_RAW_TRACES = "stream:raw-traces"
STREAM_EXECUTION_PATHS = "stream:execution-paths"
GROUP_TRACES = "perf-tracker-traces"
GROUP_PATHS = "perf-tracker-paths"

messages_processed_total: int = 0
messages_failed_total: int = 0
metrics_written_total: int = 0


def extract_metrics_from_raw_trace(data: dict) -> list[PerfMetric]:
    metrics: list[PerfMetric] = []
    repo = data.get("repo", "")
    commit_sha = data.get("commit_sha")
    entrypoint_id = data.get("entrypoint_stable_id", "")
    duration_ms = data.get("duration_ms", 0.0)
    events = data.get("events", [])

    metrics.append(PerfMetric(
        metric_type="user_code", metric_name="entrypoint_duration",
        entity_id=entrypoint_id, repo=repo,
        value=duration_ms, unit="ms", commit_sha=commit_sha,
    ))

    call_stack: list[dict] = []
    fn_durations: dict[str, list[float]] = {}
    fn_counts: dict[str, int] = {}

    for event in events:
        fn_key = f"{event['file']}:{event['fn']}"
        if event["type"] == "call":
            call_stack.append(event)
            fn_counts[fn_key] = fn_counts.get(fn_key, 0) + 1
        elif event["type"] == "return" and call_stack:
            for i in range(len(call_stack) - 1, -1, -1):
                if call_stack[i]["fn"] == event["fn"]:
                    call_event = call_stack.pop(i)
                    duration = event["timestamp_ms"] - call_event["timestamp_ms"]
                    fn_durations.setdefault(fn_key, []).append(duration)
                    break

    for fn_key, durations in fn_durations.items():
        avg = sum(durations) / len(durations)
        metrics.append(PerfMetric(
            metric_type="user_code", metric_name="fn_duration",
            entity_id=fn_key, repo=repo,
            value=avg, unit="ms", commit_sha=commit_sha,
        ))

    for fn_key, count in fn_counts.items():
        metrics.append(PerfMetric(
            metric_type="user_code", metric_name="call_frequency",
            entity_id=fn_key, repo=repo,
            value=count, unit="count", commit_sha=commit_sha,
        ))

    return metrics


def extract_metrics_from_execution_path(data: dict) -> list[PerfMetric]:
    metrics: list[PerfMetric] = []
    repo = data.get("repo", "")
    commit_sha = data.get("commit_sha")
    entrypoint_id = data.get("entrypoint_stable_id", "")
    call_sequence = data.get("call_sequence", [])
    side_effects = data.get("side_effects", [])

    max_hop = max((item["hop"] for item in call_sequence), default=-1)
    metrics.append(PerfMetric(
        metric_type="user_code", metric_name="call_depth",
        entity_id=entrypoint_id, repo=repo,
        value=max_hop + 1, unit="count", commit_sha=commit_sha,
    ))

    metrics.append(PerfMetric(
        metric_type="user_code", metric_name="side_effect_count",
        entity_id=entrypoint_id, repo=repo,
        value=len(side_effects), unit="count", commit_sha=commit_sha,
    ))

    timing_p50 = data.get("timing_p50_ms", 0.0)
    timing_p99 = data.get("timing_p99_ms", 0.0)
    metrics.append(PerfMetric(
        metric_type="user_code", metric_name="timing_p50",
        entity_id=entrypoint_id, repo=repo,
        value=timing_p50, unit="ms", commit_sha=commit_sha,
    ))
    metrics.append(PerfMetric(
        metric_type="user_code", metric_name="timing_p99",
        entity_id=entrypoint_id, repo=repo,
        value=timing_p99, unit="ms", commit_sha=commit_sha,
    ))

    return metrics


async def _collect_pipeline_metrics(redis_client: aioredis.Redis) -> list[PerfMetric]:
    metrics: list[PerfMetric] = []
    for stream in [STREAM_RAW_TRACES, STREAM_EXECUTION_PATHS]:
        depth = await redis_client.xlen(stream)
        metrics.append(PerfMetric(
            metric_type="pipeline", metric_name="queue_depth",
            entity_id=stream, repo="_pipeline",
            value=depth, unit="count",
        ))
    return metrics


async def run_collector(store: PerfStore, redis_client: aioredis.Redis) -> None:
    global messages_processed_total, messages_failed_total, metrics_written_total

    consumer_name = f"perf-tracker-{socket.gethostname()}"
    realtime_interval = int(os.environ.get("REALTIME_INTERVAL_S", "5"))
    history_interval = int(os.environ.get("HISTORY_INTERVAL_S", "60"))

    for stream, group in [
        (STREAM_RAW_TRACES, GROUP_TRACES),
        (STREAM_EXECUTION_PATHS, GROUP_PATHS),
    ]:
        try:
            await redis_client.xgroup_create(stream, group, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    logger.info("Collector started: consumer=%s", consumer_name)

    pending_metrics: list[PerfMetric] = []
    last_realtime = 0.0
    last_history = 0.0
    trace_timestamps: dict[str, float] = {}
    _MAX_TRACE_TIMESTAMPS = 10000
    throughput_counts: dict[str, list[float]] = {}

    while True:
        try:
            now = time.monotonic()

            messages = await redis_client.xreadgroup(
                groupname=GROUP_TRACES,
                consumername=consumer_name,
                streams={STREAM_RAW_TRACES: ">"},
                count=10,
                block=500,
            )
            for _stream, events in (messages or []):
                for msg_id, data in events:
                    try:
                        raw = data.get(b"event") or data.get("event")
                        if raw is None:
                            raise KeyError("missing 'event' key")
                        if isinstance(raw, bytes):
                            raw = raw.decode()
                        parsed = json.loads(raw)
                        new_metrics = extract_metrics_from_raw_trace(parsed)
                        pending_metrics.extend(new_metrics)
                        ts_ms = int(msg_id.decode().split("-")[0]) if isinstance(msg_id, bytes) else int(msg_id.split("-")[0])
                        repo = parsed.get("repo", "")
                        sid = parsed.get("entrypoint_stable_id", "")
                        trace_timestamps[f"{repo}:{sid}"] = ts_ms
                        if len(trace_timestamps) > _MAX_TRACE_TIMESTAMPS:
                            oldest_key = next(iter(trace_timestamps))
                            del trace_timestamps[oldest_key]
                        throughput_counts.setdefault(STREAM_RAW_TRACES, []).append(time.monotonic())
                        await redis_client.xack(STREAM_RAW_TRACES, GROUP_TRACES, msg_id)
                        messages_processed_total += 1
                    except (json.JSONDecodeError, ValidationError, KeyError) as exc:
                        logger.warning("Bad raw-trace %s, skipping: %s", msg_id, exc)
                        messages_failed_total += 1
                        await redis_client.xack(STREAM_RAW_TRACES, GROUP_TRACES, msg_id)

            messages = await redis_client.xreadgroup(
                groupname=GROUP_PATHS,
                consumername=consumer_name,
                streams={STREAM_EXECUTION_PATHS: ">"},
                count=10,
                block=500,
            )
            for _stream, events in (messages or []):
                for msg_id, data in events:
                    try:
                        raw = data.get(b"event") or data.get("event")
                        if raw is None:
                            raise KeyError("missing 'event' key")
                        if isinstance(raw, bytes):
                            raw = raw.decode()
                        parsed = json.loads(raw)
                        new_metrics = extract_metrics_from_execution_path(parsed)
                        pending_metrics.extend(new_metrics)
                        ts_ms = int(msg_id.decode().split("-")[0]) if isinstance(msg_id, bytes) else int(msg_id.split("-")[0])
                        repo = parsed.get("repo", "")
                        sid = parsed.get("entrypoint_stable_id", "")
                        key = f"{repo}:{sid}"
                        if key in trace_timestamps:
                            lag_ms = ts_ms - trace_timestamps.pop(key)
                            pending_metrics.append(PerfMetric(
                                metric_type="pipeline", metric_name="processing_lag",
                                entity_id="trace-normalizer", repo=repo,
                                value=lag_ms / 1000.0, unit="s",
                            ))
                        throughput_counts.setdefault(STREAM_EXECUTION_PATHS, []).append(time.monotonic())
                        await redis_client.xack(STREAM_EXECUTION_PATHS, GROUP_PATHS, msg_id)
                        messages_processed_total += 1
                    except (json.JSONDecodeError, ValidationError, KeyError) as exc:
                        logger.warning("Bad execution-path %s, skipping: %s", msg_id, exc)
                        messages_failed_total += 1
                        await redis_client.xack(STREAM_EXECUTION_PATHS, GROUP_PATHS, msg_id)

            cutoff = time.monotonic() - 60.0
            for stream in [STREAM_RAW_TRACES, STREAM_EXECUTION_PATHS]:
                arrivals = throughput_counts.get(stream, [])
                arrivals[:] = [t for t in arrivals if t > cutoff]
                rate = len(arrivals) / 60.0 if arrivals else 0.0
                pending_metrics.append(PerfMetric(
                    metric_type="pipeline", metric_name="stream_throughput",
                    entity_id=stream, repo="_pipeline",
                    value=rate, unit="msg/s",
                ))

            pipeline_metrics = await _collect_pipeline_metrics(redis_client)
            pending_metrics.extend(pipeline_metrics)

            if now - last_realtime >= realtime_interval:
                repos = {m.repo for m in pending_metrics if m.repo != "_pipeline"}
                for repo in repos:
                    repo_metrics = [m for m in pending_metrics if m.repo == repo]
                    snapshot: dict[str, str] = {}
                    fn_dur: dict[str, float] = {}
                    fn_freq: dict[str, float] = {}
                    for m in repo_metrics:
                        if m.metric_name == "fn_duration":
                            fn_dur[m.entity_id] = m.value
                        elif m.metric_name == "call_frequency":
                            fn_freq[m.entity_id] = m.value
                        snapshot[f"{m.metric_name}:{m.entity_id}"] = str(m.value)
                    fn_scores = {eid: dur * fn_freq.get(eid, 1.0) for eid, dur in fn_dur.items()}
                    for m in pipeline_metrics:
                        snapshot[f"{m.metric_name}:{m.entity_id}"] = str(m.value)
                    await store.update_realtime(repo, snapshot)
                    if fn_scores:
                        await store.update_hot_functions(repo, fn_scores)

                # Run bottleneck detection after each realtime cycle
                all_bottlenecks = []

                # Queue buildup detection
                curr_depths = {
                    m.entity_id: int(m.value)
                    for m in pipeline_metrics if m.metric_name == "queue_depth"
                }
                if hasattr(store, '_prev_depths'):
                    all_bottlenecks.extend(detect_queue_buildup(store._prev_depths, curr_depths, repo="_pipeline"))
                store._prev_depths = curr_depths

                # Processing lag detection
                lag_threshold = float(os.environ.get("LAG_THRESHOLD_S", "10"))
                lag_metrics = [m for m in pending_metrics if m.metric_name == "processing_lag"]
                if lag_metrics:
                    lag_values = [m.value for m in lag_metrics]
                    all_bottlenecks.extend(detect_processing_lag(lag_values, lag_threshold, repo="_pipeline"))

                # Throughput drop detection
                throughput_drop_pct = float(os.environ.get("THROUGHPUT_DROP_PCT", "50"))
                throughput_metrics = [m for m in pending_metrics if m.metric_name == "stream_throughput"]
                for tm in throughput_metrics:
                    # Use current rate as both current and rolling avg for now
                    # A proper rolling average would need historical data
                    if hasattr(store, '_prev_throughput') and tm.entity_id in store._prev_throughput:
                        all_bottlenecks.extend(detect_throughput_drop(
                            tm.value, store._prev_throughput[tm.entity_id], throughput_drop_pct,
                            tm.entity_id, repo="_pipeline",
                        ))
                if not hasattr(store, '_prev_throughput'):
                    store._prev_throughput = {}
                for tm in throughput_metrics:
                    store._prev_throughput[tm.entity_id] = tm.value

                # Slow function detection from pending metrics
                slow_rows = [
                    {"entity_id": m.entity_id, "value": m.value, "unit": m.unit}
                    for m in pending_metrics if m.metric_name == "fn_duration"
                ]
                if slow_rows:
                    slow_rows.sort(key=lambda r: r["value"], reverse=True)
                    all_bottlenecks.extend(detect_slow_functions(slow_rows[:20], repo=repos.pop() if repos else "_pipeline"))

                if all_bottlenecks:
                    bottleneck_jsons = [b.model_dump_json() for b in all_bottlenecks]
                    await store.update_bottlenecks("_pipeline", bottleneck_jsons)

                last_realtime = now

            if now - last_history >= history_interval and pending_metrics:
                await store.write_metrics(pending_metrics)
                metrics_written_total += len(pending_metrics)
                pending_metrics = []
                last_history = now

        except asyncio.CancelledError:
            if pending_metrics:
                try:
                    await store.write_metrics(pending_metrics)
                except Exception:
                    logger.warning("Failed to flush metrics on shutdown")
            raise
        except Exception as exc:
            logger.error("Collector loop error: %s", exc)
            await asyncio.sleep(1)
