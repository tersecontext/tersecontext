# services/trace-normalizer/app/consumer.py
from __future__ import annotations

import asyncio
import json
import logging

from shared.consumer import RedisConsumerBase

from .classifier import classify_io_events, classify_side_effects
from .emitter import emit_execution_path
from .models import ExecutionPath, RawTrace
from .normalizer import aggregate_frequencies, compute_percentiles, reconstruct_call_tree
from .reconciler import reconcile

logger = logging.getLogger(__name__)


class NormalizerConsumer(RedisConsumerBase):
    stream = "stream:raw-traces"
    group = "normalizer-group"

    def __init__(self, redis_sync, neo4j_driver) -> None:
        # redis_sync: sync redis.Redis used by aggregate_frequencies, reconcile, emit_execution_path
        # neo4j_driver: used by reconcile
        self._redis_sync = redis_sync
        self._driver = neo4j_driver
        self.events_processed: int = 0

    async def handle(self, data: dict) -> None:
        raw = data.get(b"event") or data.get("event")
        if raw is None:
            raise KeyError("missing 'event' key")
        if isinstance(raw, bytes):
            raw = raw.decode()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._process_sync, self._redis_sync, self._driver, raw
        )
        self.events_processed += 1

    @staticmethod
    def _process_sync(r, driver, raw_json: str) -> None:
        trace = RawTrace.model_validate_json(raw_json)
        nodes = reconstruct_call_tree(trace.events)

        repo = trace.repo or "unknown"
        agg_key = f"trace_agg:{repo}:{trace.entrypoint_stable_id}"
        raw_agg = r.get(agg_key)
        existing_agg = json.loads(raw_agg) if raw_agg else {}
        max_runs = max((v.get("runs", 0) for v in existing_agg.values()), default=0) + 1

        nodes = aggregate_frequencies(r, repo, trace.entrypoint_stable_id, nodes, max_runs)
        side_effects = classify_side_effects(trace.events)
        if trace.io_events:
            io_side_effects = classify_io_events(trace.io_events)
            # io_events carry actual SQL/URL content; suppress generic fn-name
            # effects for the same types, but keep trace-based cache detection.
            io_types = {e.type for e in io_side_effects}
            side_effects = io_side_effects + [e for e in side_effects if e.type not in io_types]
        observed_fns = {ev.fn for ev in trace.events if ev.type == "call"}
        dynamic_only, never_observed = reconcile(driver, repo, trace.entrypoint_stable_id, observed_fns)

        durations = [n.avg_ms for n in nodes if n.avg_ms > 0]
        p50, p99 = compute_percentiles(durations or [trace.duration_ms])

        coverage_pct: float | None = None
        if nodes:
            observed = sum(1 for n in nodes if n.frequency_ratio > 0.0)
            coverage_pct = observed / len(nodes)

        ep = ExecutionPath(
            entrypoint_stable_id=trace.entrypoint_stable_id,
            commit_sha=trace.commit_sha,
            repo=repo,
            call_sequence=nodes,
            side_effects=side_effects,
            dynamic_only_edges=dynamic_only,
            never_observed_static_edges=never_observed,
            timing_p50_ms=p50,
            timing_p99_ms=p99,
            coverage_pct=coverage_pct,
        )
        emit_execution_path(r, ep)
