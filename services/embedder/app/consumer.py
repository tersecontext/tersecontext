import asyncio
import json
import logging
import os
import socket

import redis.asyncio as aioredis
import redis.exceptions
from pydantic import ValidationError

from .embedder import embed_nodes
from .models import EmbeddedNodesEvent, ParsedFileEvent
from .neo4j_client import Neo4jClient
from .providers.base import EmbeddingProvider

logger = logging.getLogger(__name__)

STREAM_IN = "stream:parsed-file"
STREAM_OUT = "stream:embedded-nodes"
GROUP = "embedder-group"


async def _process(
    r: aioredis.Redis,
    data: dict,
    provider: EmbeddingProvider,
    neo4j_client: Neo4jClient,
    batch_size: int,
    embedding_dim: int,
) -> None:
    raw = data.get(b"event") or data.get("event")
    if raw is None:
        raise KeyError("message missing 'event' key")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")

    event = ParsedFileEvent.model_validate_json(raw)

    stable_ids = [n.stable_id for n in event.nodes]
    try:
        neo4j_cache = await neo4j_client.get_node_hashes(stable_ids)
    except Exception as exc:
        logger.warning(
            "Neo4j unreachable, treating all %d nodes as new: %s",
            len(stable_ids), exc,
        )
        neo4j_cache = {}

    embedded = await embed_nodes(
        event.nodes, neo4j_cache, provider, batch_size, embedding_dim,
        file_path=event.file_path,
        language=event.language,
    )

    out = EmbeddedNodesEvent(
        repo=event.repo,
        commit_sha=event.commit_sha,
        file_path=event.file_path,
        nodes=embedded,
    )
    await r.xadd(STREAM_OUT, {"event": out.model_dump_json()})


async def run_consumer(
    provider: EmbeddingProvider,
    neo4j_client: Neo4jClient,
    batch_size: int = 64,
    embedding_dim: int = 0,
) -> None:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = aioredis.from_url(url)
    try:
        consumer_name = f"embedder-{socket.gethostname()}"

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
                            await _process(
                                r, data, provider, neo4j_client, batch_size, embedding_dim
                            )
                            await r.xack(STREAM_IN, GROUP, msg_id)
                        except (json.JSONDecodeError, ValidationError, KeyError) as exc:
                            logger.warning(
                                "Bad message %s, skipping (XACK): %s", msg_id, exc
                            )
                            await r.xack(STREAM_IN, GROUP, msg_id)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.error("Failed msg_id=%s: %s", msg_id, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Consumer loop error: %s", exc)
    finally:
        await r.aclose()
