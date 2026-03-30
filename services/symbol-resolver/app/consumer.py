# services/symbol-resolver/app/consumer.py
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from datetime import datetime, timezone

import redis as redis_sync
import redis.asyncio as aioredis
import redis.exceptions
from pydantic import ValidationError

from .models import ParsedFileEvent, PendingRef
from .pending import push_pending, retry_pending
from .resolver import compute_path_hint, parse_import_body, resolve_import, write_package_edge

logger = logging.getLogger(__name__)

STREAM = "stream:parsed-file"
GROUP = "symbol-resolver-group"


async def run_consumer(driver) -> None:
    """
    Main consumer coroutine. Uses two Redis clients:
      - r      (async, aioredis): for xgroup_create, xreadgroup, xack — must be non-blocking
      - r_sync (sync, redis):     passed to _process_event via run_in_executor for pending_refs
    """
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = aioredis.from_url(url)
    r_sync = redis_sync.from_url(url, decode_responses=False)
    consumer_name = f"symbol-resolver-{socket.gethostname()}"

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
                for _stream, events in (messages or []):
                    for msg_id, data in events:
                        try:
                            raw = data.get(b"event") or data.get("event")
                            if raw is None:
                                raise KeyError("missing 'event' key")
                            if isinstance(raw, bytes):
                                raw = raw.decode("utf-8")
                            event = ParsedFileEvent.model_validate_json(raw)

                            await loop.run_in_executor(None, _process_event, driver, r_sync, event)

                            await r.xack(STREAM, GROUP, msg_id)
                        except (json.JSONDecodeError, ValidationError, KeyError) as exc:
                            logger.warning("Bad message %s, skipping: %s", msg_id, exc)
                            await r.xack(STREAM, GROUP, msg_id)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.error("Consumer failed msg=%s: %s", msg_id, exc)
                            await r.xack(STREAM, GROUP, msg_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Consumer loop error: %s", exc)
    finally:
        await r.aclose()
        r_sync.close()


def _process_event(driver, r, event: ParsedFileEvent) -> None:
    """
    Process a single ParsedFileEvent synchronously (called via run_in_executor).
    r is a sync Redis client; driver is a sync Neo4j driver.

    For each import node:
      - Plain 'import X' -> write_package_edge (package race logged as warning, not retried)
      - 'from X import Y' -> resolve_import; if unresolved, push_pending

    After processing all imports, retry pending_refs for this repo.
    """
    import_nodes = [n for n in event.nodes if n.type == "import"]

    # NOTE: Cross-file CALLS edge resolution is deferred to a future version.
    # The parser only generates intra-file CALLS edges.
    now = datetime.now(timezone.utc)

    for node in import_nodes:
        info = parse_import_body(node.body)

        if info["kind"] == "plain":
            for pkg_name in info["names"]:
                if not write_package_edge(driver, node.stable_id, pkg_name, event.repo):
                    # Importer node not yet in Neo4j. Package edges are not retried in v1
                    # since external packages are never targets of other lookups.
                    logger.warning(
                        "Importer not yet in Neo4j for package edge %s -> %s, skipping",
                        node.stable_id, pkg_name,
                    )
        else:
            # from-import
            path_hint = compute_path_hint(
                module=info["module"],
                file_path=event.file_path,
                dots=info["dots"],
            )
            for symbol in info["names"]:
                resolved = resolve_import(
                    driver,
                    node.stable_id,
                    symbol,
                    path_hint,
                    event.repo,
                )
                if not resolved:
                    ref = PendingRef(
                        source_stable_id=node.stable_id,
                        target_name=symbol,
                        path_hint=path_hint,
                        repo=event.repo,
                        attempted_at=now,
                    )
                    push_pending(r, ref)

    # Retry any pending refs — new nodes may now satisfy old unresolved imports
    retry_pending(driver, r, event.repo)
