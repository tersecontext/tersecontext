from __future__ import annotations
import asyncio
import json
import logging
import os
import socket

import redis.asyncio as aioredis
import redis.exceptions
from pydantic import ValidationError

from app.models import ExecutionPath
from app.renderer import render_spec_text
from app.store import SpecStore

logger = logging.getLogger(__name__)

STREAM_IN = "stream:execution-paths"
GROUP = "spec-generator-group"

messages_processed_total: int = 0
messages_failed_total: int = 0
specs_written_total: int = 0
specs_embedded_total: int = 0


async def _process(data: dict, store: SpecStore) -> None:
    global specs_written_total, specs_embedded_total
    raw = data.get(b"event") or data.get("event")
    if raw is None:
        raise KeyError("message missing 'event' key")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")

    path = ExecutionPath.model_validate_json(raw)

    entrypoint_name = (
        path.call_sequence[0].name if path.call_sequence else path.entrypoint_stable_id
    )

    spec_text = render_spec_text(path, entrypoint_name)
    await store.upsert_spec(path, spec_text)
    specs_written_total += 1
    await store.upsert_qdrant(path, entrypoint_name, spec_text)
    specs_embedded_total += 1


async def run_consumer(store: SpecStore) -> None:
    global messages_processed_total, messages_failed_total

    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = aioredis.from_url(url)
    consumer_name = f"spec-generator-{socket.gethostname()}"
    try:
        try:
            await r.xgroup_create(STREAM_IN, GROUP, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        logger.info("Consumer started: group=%s consumer=%s", GROUP, consumer_name)

        while True:
            try:
                messages = await r.xreadgroup(
                    groupname=GROUP,
                    consumername=consumer_name,
                    streams={STREAM_IN: ">"},
                    count=10,
                    block=1000,
                )
                for _stream, events in (messages or []):
                    for msg_id, data in events:
                        try:
                            await _process(data, store)
                            await r.xack(STREAM_IN, GROUP, msg_id)
                            messages_processed_total += 1
                        except (json.JSONDecodeError, ValidationError, KeyError) as exc:
                            logger.warning("Bad message %s, skipping (XACK): %s", msg_id, exc)
                            messages_failed_total += 1
                            await r.xack(STREAM_IN, GROUP, msg_id)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.error("Failed msg_id=%s, will retry: %s", msg_id, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Consumer loop error: %s", exc)
    finally:
        await r.aclose()
