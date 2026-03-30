from __future__ import annotations
import asyncio
import logging
import os
import socket

import redis.asyncio as aioredis
import redis.exceptions
from pydantic import ValidationError

from .models import EmbeddedNodesEvent, FileChangedEvent
from .writer import QdrantWriter

logger = logging.getLogger(__name__)

STREAM_EMBEDDED = "stream:embedded-nodes"
GROUP_EMBEDDED = "vector-writer-group"

STREAM_FILE_CHANGED = "stream:file-changed"
GROUP_DELETE = "vector-writer-delete-group"


async def _process_embedded(writer: QdrantWriter, data: dict) -> None:
    """Parse an embedded-nodes message and upsert to Qdrant.

    Raises on Qdrant failure (caller must NOT XACK).
    Raises ValidationError/ValueError on malformed JSON (caller should XACK).
    Returns normally on success or empty nodes list (caller should XACK).
    """
    raw = data.get(b"event") or data.get("event")
    if raw is None:
        raise KeyError("message missing 'event' key")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    event = EmbeddedNodesEvent.model_validate_json(raw)
    if not event.nodes:
        return
    await writer.upsert_points(event)


async def _process_delete(writer: QdrantWriter, data: dict) -> None:
    """Parse a file-changed message and delete removed nodes from Qdrant.

    Raises on Qdrant failure (caller must NOT XACK).
    Raises ValidationError/ValueError on malformed JSON (caller should XACK).
    Returns normally on success or empty deleted_nodes (caller should XACK).
    """
    raw = data.get(b"event") or data.get("event")
    if raw is None:
        raise KeyError("message missing 'event' key")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    event = FileChangedEvent.model_validate_json(raw)
    if not event.deleted_nodes:
        return
    await writer.delete_points(event.deleted_nodes)


async def run_embedded_consumer(writer: QdrantWriter) -> None:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = aioredis.from_url(url)
    consumer_name = f"vector-writer-{socket.gethostname()}"
    try:
        try:
            await r.xgroup_create(STREAM_EMBEDDED, GROUP_EMBEDDED, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        logger.info("Embedded consumer started: group=%s consumer=%s", GROUP_EMBEDDED, consumer_name)

        while True:
            try:
                messages = await r.xreadgroup(
                    groupname=GROUP_EMBEDDED,
                    consumername=consumer_name,
                    streams={STREAM_EMBEDDED: ">"},
                    count=10,
                    block=1000,
                )
                for _stream, events in (messages or []):
                    for msg_id, data in events:
                        try:
                            await _process_embedded(writer, data)
                            await r.xack(STREAM_EMBEDDED, GROUP_EMBEDDED, msg_id)
                        except (ValidationError, ValueError, KeyError) as exc:
                            logger.warning("Bad message %s, skipping (XACK): %s", msg_id, exc)
                            await r.xack(STREAM_EMBEDDED, GROUP_EMBEDDED, msg_id)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.error("Failed msg_id=%s, will retry: %s", msg_id, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Embedded consumer loop error: %s", exc)
    finally:
        await r.aclose()


async def run_delete_consumer(writer: QdrantWriter) -> None:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = aioredis.from_url(url)
    consumer_name = f"vector-writer-delete-{socket.gethostname()}"
    try:
        try:
            await r.xgroup_create(STREAM_FILE_CHANGED, GROUP_DELETE, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        logger.info("Delete consumer started: group=%s consumer=%s", GROUP_DELETE, consumer_name)

        while True:
            try:
                messages = await r.xreadgroup(
                    groupname=GROUP_DELETE,
                    consumername=consumer_name,
                    streams={STREAM_FILE_CHANGED: ">"},
                    count=10,
                    block=1000,
                )
                for _stream, events in (messages or []):
                    for msg_id, data in events:
                        try:
                            await _process_delete(writer, data)
                            await r.xack(STREAM_FILE_CHANGED, GROUP_DELETE, msg_id)
                        except (ValidationError, ValueError, KeyError) as exc:
                            logger.warning("Bad delete message %s, skipping (XACK): %s", msg_id, exc)
                            await r.xack(STREAM_FILE_CHANGED, GROUP_DELETE, msg_id)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.error("Failed delete msg_id=%s, will retry: %s", msg_id, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Delete consumer loop error: %s", exc)
    finally:
        await r.aclose()
