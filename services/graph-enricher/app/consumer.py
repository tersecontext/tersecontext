# services/graph-enricher/app/consumer.py
from __future__ import annotations

import asyncio
import logging

from pydantic import ValidationError
from shared.consumer import RedisConsumerBase

from . import enricher
from .models import ExecutionPath

logger = logging.getLogger(__name__)


def _extract_records(event: ExecutionPath) -> tuple[list[dict], list[dict], list[str]]:
    """Extract (node_records, dynamic_edges, observed_ids) from an ExecutionPath."""
    node_records = [
        {
            "stable_id": node.stable_id,
            "avg_latency_ms": node.avg_ms,
            "branch_coverage": node.frequency_ratio,
        }
        for node in event.call_sequence
    ]
    observed_in_sequence = {r["stable_id"] for r in node_records}
    if event.entrypoint_stable_id not in observed_in_sequence:
        node_records.append({
            "stable_id": event.entrypoint_stable_id,
            "avg_latency_ms": event.timing_p50_ms,
            "branch_coverage": 1.0,
        })

    dynamic_edges = [
        {"source": e.source, "target": e.target, "count": 1}
        for e in event.dynamic_only_edges
    ]

    observed_ids = [node.stable_id for node in event.call_sequence]
    if event.entrypoint_stable_id not in observed_ids:
        observed_ids.insert(0, event.entrypoint_stable_id)

    return node_records, dynamic_edges, observed_ids


class GraphEnricherConsumer(RedisConsumerBase):
    stream = "stream:execution-paths"
    group = "graph-enricher-group"

    def __init__(self, driver) -> None:
        self._driver = driver
        self._batch_node_records: list[dict] = []
        self._batch_dynamic_edges: list[dict] = []
        self._batch_observed_ids: list[str] = []

    async def handle(self, data: dict) -> None:
        raw = data.get(b"event") or data.get("event")
        if raw is None:
            raise KeyError("missing 'event' key")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            event = ExecutionPath.model_validate_json(raw)
        except ValidationError as exc:
            raise ValueError(f"invalid ExecutionPath JSON: {exc}") from exc
        node_records, dynamic_edges, observed_ids = _extract_records(event)
        self._batch_node_records.extend(node_records)
        self._batch_dynamic_edges.extend(dynamic_edges)
        self._batch_observed_ids.extend(observed_ids)

    async def post_batch(self) -> None:
        if not self._batch_node_records:
            return
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None, enricher.update_node_props_batch, self._driver, self._batch_node_records
            )
            await loop.run_in_executor(
                None, enricher.upsert_dynamic_edges, self._driver, self._batch_dynamic_edges
            )
            await loop.run_in_executor(
                None, enricher.confirm_static_edges, self._driver, self._batch_observed_ids
            )
            await loop.run_in_executor(None, enricher.run_conflict_detector, self._driver)
            await loop.run_in_executor(None, enricher.run_staleness_downgrade, self._driver)
        except Exception as exc:
            logger.error("Batch Neo4j write failed: %s", exc)
        finally:
            self._batch_node_records.clear()
            self._batch_dynamic_edges.clear()
            self._batch_observed_ids.clear()


# ── Backward-compat shims — existing tests import these names ──────────────
# The existing test file imports: _process_event, run_consumer, STREAM, GROUP

STREAM = GraphEnricherConsumer.stream
GROUP = GraphEnricherConsumer.group


def _process_event(driver, event: ExecutionPath) -> None:
    """Used by existing unit tests. New code uses GraphEnricherConsumer."""
    node_records, dynamic_edges, observed_ids = _extract_records(event)
    enricher.update_node_props_batch(driver, node_records)
    enricher.upsert_dynamic_edges(driver, dynamic_edges)
    enricher.confirm_static_edges(driver, observed_ids)


async def run_consumer(driver) -> None:
    """Used by existing unit tests. New code calls GraphEnricherConsumer.run() directly."""
    redis_url = __import__("os").environ.get("REDIS_URL", "redis://localhost:6379")
    await GraphEnricherConsumer(driver).run(redis_url)
