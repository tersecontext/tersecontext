from __future__ import annotations

import asyncio
import json
import logging
import os
import socket

import redis.asyncio as aioredis
import redis.exceptions
from pydantic import ValidationError

from . import writer
from .models import EmbeddedNodesEvent, ParsedFileEvent

logger = logging.getLogger(__name__)

STREAM_EMBEDDED = "stream:embedded-nodes"
STREAM_PARSED = "stream:parsed-file"
GROUP_NODES = "graph-writer-group"
GROUP_EDGES = "graph-writer-edges-group"

# Keyed by (commit_sha, file_path). Edge consumer writes; node consumer reads.
_parsed_file_cache: dict[tuple[str, str], ParsedFileEvent] = {}


async def run_edge_consumer(driver) -> None:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = aioredis.from_url(url)
    consumer_name = f"graph-writer-{socket.gethostname()}"
    try:
        try:
            await r.xgroup_create(STREAM_PARSED, GROUP_EDGES, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        logger.info("Edge consumer started: group=%s consumer=%s", GROUP_EDGES, consumer_name)
        loop = asyncio.get_running_loop()

        while True:
            try:
                messages = await r.xreadgroup(
                    groupname=GROUP_EDGES,
                    consumername=consumer_name,
                    streams={STREAM_PARSED: ">"},
                    count=10,
                    block=1000,
                )
                for _stream, events in (messages or []):
                    for msg_id, data in events:
                        try:
                            raw = data.get(b"event") or data.get("event")
                            if raw is None:
                                raise KeyError("missing 'event' key")
                            if isinstance(raw, bytes):
                                raw = raw.decode("utf-8")
                            event = ParsedFileEvent.model_validate_json(raw)

                            _parsed_file_cache[(event.commit_sha, event.file_path)] = event

                            edges = [
                                {"source": e.source_stable_id, "target": e.target_stable_id}
                                for e in event.intra_file_edges
                            ]
                            await loop.run_in_executor(None, writer.upsert_edges, driver, edges)

                            if event.deleted_nodes:
                                await loop.run_in_executor(
                                    None, writer.tombstone, driver, event.deleted_nodes
                                )

                            await r.xack(STREAM_PARSED, GROUP_EDGES, msg_id)
                        except (json.JSONDecodeError, ValidationError, KeyError) as exc:
                            logger.warning("Bad edge message %s, skipping: %s", msg_id, exc)
                            await r.xack(STREAM_PARSED, GROUP_EDGES, msg_id)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.error("Edge consumer failed msg=%s: %s", msg_id, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Edge consumer loop error: %s", exc)
    finally:
        await r.aclose()


async def run_node_consumer(driver) -> None:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = aioredis.from_url(url)
    consumer_name = f"graph-writer-{socket.gethostname()}"
    try:
        try:
            await r.xgroup_create(STREAM_EMBEDDED, GROUP_NODES, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        logger.info("Node consumer started: group=%s consumer=%s", GROUP_NODES, consumer_name)
        loop = asyncio.get_running_loop()

        while True:
            try:
                messages = await r.xreadgroup(
                    groupname=GROUP_NODES,
                    consumername=consumer_name,
                    streams={STREAM_EMBEDDED: ">"},
                    count=10,
                    block=1000,
                )
                for _stream, events in (messages or []):
                    for msg_id, data in events:
                        try:
                            raw = data.get(b"event") or data.get("event")
                            if raw is None:
                                raise KeyError("missing 'event' key")
                            if isinstance(raw, bytes):
                                raw = raw.decode("utf-8")
                            event = EmbeddedNodesEvent.model_validate_json(raw)

                            parsed_event = _parsed_file_cache.get(
                                (event.commit_sha, event.file_path)
                            )
                            if parsed_event is None:
                                logger.warning(
                                    "Cache miss: commit_sha=%s file_path=%s — skipping",
                                    event.commit_sha, event.file_path,
                                )
                                await r.xack(STREAM_EMBEDDED, GROUP_NODES, msg_id)
                                continue

                            parsed_by_id = {n.stable_id: n for n in parsed_event.nodes}
                            records = writer.build_node_records(
                                event.nodes,
                                parsed_by_id,
                                parsed_event.file_path,
                                parsed_event.language,
                                parsed_event.repo,
                            )
                            await loop.run_in_executor(
                                None, writer.upsert_nodes, driver, records
                            )
                            await r.xack(STREAM_EMBEDDED, GROUP_NODES, msg_id)
                        except (json.JSONDecodeError, ValidationError, KeyError) as exc:
                            logger.warning("Bad node message %s, skipping: %s", msg_id, exc)
                            await r.xack(STREAM_EMBEDDED, GROUP_NODES, msg_id)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.error("Node consumer failed msg=%s: %s", msg_id, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Node consumer loop error: %s", exc)
    finally:
        await r.aclose()
