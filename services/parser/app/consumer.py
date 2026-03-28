import asyncio
import logging
import os
import socket

import redis
import redis.exceptions

from .extractor import apply_diff_filter, extract
from .models import FileChangedEvent, ParsedFileEvent
from .parser import parse

logger = logging.getLogger(__name__)

STREAM_IN = "stream:file-changed"
STREAM_OUT = "stream:parsed-file"
GROUP = "parser-group"


def _get_redis() -> redis.Redis:
    return redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))


def _process(r: redis.Redis, data: dict) -> None:
    raw = data.get(b"event") or data.get("event")
    if raw is None:
        raise KeyError("message missing 'event' key")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    event = FileChangedEvent.model_validate_json(raw)

    repo_root = os.environ.get("REPO_ROOT", "/repos")

    if event.diff_type == "deleted":
        out = ParsedFileEvent(
            file_path=event.path,
            language=event.language,
            repo=event.repo,
            commit_sha=event.commit_sha,
            nodes=[],
            intra_file_edges=[],
            deleted_nodes=event.deleted_nodes,
        )
    else:
        file_path = os.path.join(repo_root, event.path)
        with open(file_path, "rb") as fh:
            source_bytes = fh.read()

        root = parse(source_bytes, event.language)
        all_nodes, all_edges = extract(root, source_bytes, event.repo, event.path)
        filtered_nodes, filtered_edges = apply_diff_filter(all_nodes, all_edges, event)

        out = ParsedFileEvent(
            file_path=event.path,
            language=event.language,
            repo=event.repo,
            commit_sha=event.commit_sha,
            nodes=filtered_nodes,
            intra_file_edges=filtered_edges,
            deleted_nodes=event.deleted_nodes,
        )

    r.xadd(STREAM_OUT, {"event": out.model_dump_json()})


def _consumer_loop() -> None:
    r = _get_redis()
    consumer_name = f"parser-{socket.gethostname()}"

    try:
        r.xgroup_create(STREAM_IN, GROUP, id="0", mkstream=True)
    except redis.exceptions.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise

    logger.info("Consumer started: group=%s consumer=%s", GROUP, consumer_name)

    while True:
        try:
            messages = r.xreadgroup(
                groupname=GROUP,
                consumername=consumer_name,
                streams={STREAM_IN: ">"},
                count=10,
                block=1000,
            )
            for _stream, events in (messages or []):
                for msg_id, data in events:
                    try:
                        _process(r, data)
                        r.xack(STREAM_IN, GROUP, msg_id)
                    except Exception as exc:
                        logger.error("Failed msg_id=%s: %s", msg_id, exc)
        except Exception as exc:
            logger.error("Consumer loop error: %s", exc)


async def run_consumer() -> None:
    """Run the blocking consumer loop in a thread so it doesn't block the event loop.

    Note: cancelling this coroutine will not stop the background thread immediately —
    _consumer_loop runs until the process exits. This is acceptable for this service.
    """
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _consumer_loop)
    except asyncio.CancelledError:
        pass
