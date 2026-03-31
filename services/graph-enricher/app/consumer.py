from __future__ import annotations

import asyncio
import logging
import os
import socket

import redis.asyncio as aioredis
import redis.exceptions
from pydantic import ValidationError

from . import enricher
from .models import ExecutionPath

logger = logging.getLogger(__name__)

STREAM = "stream:execution-paths"
GROUP = "graph-enricher-group"


async def run_consumer(driver) -> None:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = aioredis.from_url(url)
    consumer_name = f"graph-enricher-{socket.gethostname()}"
    try:
        try:
            await r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        logger.info("Consumer started: group=%s consumer=%s", GROUP, consumer_name)
        loop = asyncio.get_running_loop()

        while True:
            try:
                messages = await r.xreadgroup(
                    groupname=GROUP,
                    consumername=consumer_name,
                    streams={STREAM: ">"},
                    count=10,
                    block=1000,
                )
                any_event_written = False
                for _stream, events in (messages or []):
                    for msg_id, data in events:
                        try:
                            raw = data.get(b"event") or data.get("event")
                            if raw is None:
                                raise KeyError("missing 'event' key")
                            if isinstance(raw, bytes):
                                raw = raw.decode("utf-8")
                            event = ExecutionPath.model_validate_json(raw)
                            await loop.run_in_executor(None, _process_event, driver, event)
                            any_event_written = True
                            await r.xack(STREAM, GROUP, msg_id)
                        except (ValidationError, KeyError) as exc:
                            logger.warning("Bad message %s, skipping: %s", msg_id, exc)
                            await r.xack(STREAM, GROUP, msg_id)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.error("Consumer failed msg=%s: %s", msg_id, exc)

                if any_event_written:
                    try:
                        await loop.run_in_executor(None, enricher.run_conflict_detector, driver)
                        await loop.run_in_executor(None, enricher.run_staleness_downgrade, driver)
                    except Exception as exc:
                        logger.error("Post-batch detector failed: %s", exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Consumer loop error: %s", exc)
    finally:
        await r.aclose()


def _process_event(driver, event: ExecutionPath) -> None:
    # Update dynamic properties for every node in the call sequence
    node_records = [
        {
            "stable_id": node.stable_id,
            "avg_latency_ms": node.avg_ms,
            "branch_coverage": node.frequency_ratio,
        }
        for node in event.call_sequence
    ]
    enricher.update_node_props_batch(driver, node_records)

    # Write dynamic-only edges
    dynamic_edges = [
        {"source": e.source, "target": e.target, "count": 1}
        for e in event.dynamic_only_edges
    ]
    enricher.upsert_dynamic_edges(driver, dynamic_edges)

    # Confirm static edges between observed nodes
    observed_ids = [node.stable_id for node in event.call_sequence]
    if event.entrypoint_stable_id not in observed_ids:
        observed_ids.insert(0, event.entrypoint_stable_id)
    enricher.confirm_static_edges(driver, observed_ids)
