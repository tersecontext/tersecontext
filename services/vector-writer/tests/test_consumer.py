# services/vector-writer/tests/test_consumer.py
import json
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import redis.exceptions
from pydantic import ValidationError

from app.consumer import (
    run_embedded_consumer,
    run_delete_consumer,
    STREAM_EMBEDDED,
    GROUP_EMBEDDED,
    STREAM_FILE_CHANGED,
    GROUP_DELETE,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_embedded_event_json():
    return json.dumps({
        "repo": "acme",
        "commit_sha": "abc123",
        "nodes": [{
            "stable_id": "sha256:abc",
            "vector": [0.1, 0.2, 0.3],
            "name": "authenticate",
            "type": "function",
            "file_path": "auth/service.py",
            "language": "python",
            "node_hash": "sha256:def",
        }],
    })


def _make_delete_event_json():
    return json.dumps({
        "repo": "acme",
        "deleted_nodes": ["sha256:abc", "sha256:def"],
    })


def _make_mock_redis():
    r = AsyncMock()
    r.xgroup_create = AsyncMock()
    r.xreadgroup = AsyncMock(return_value=[])
    r.xack = AsyncMock()
    r.aclose = AsyncMock()
    return r


def _xreadgroup_then_cancel(stream_name, messages):
    """Return messages on first call, then raise CancelledError to exit loop."""
    call_count = 0

    async def side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [(stream_name, messages)]
        raise asyncio.CancelledError()

    return side_effect


# ── Embedded consumer: group creation ──────────────────────────────────────────

async def test_embedded_consumer_creates_group():
    r = _make_mock_redis()
    r.xreadgroup.side_effect = asyncio.CancelledError()
    writer = AsyncMock()

    with patch("app.consumer.aioredis.from_url", return_value=r):
        with pytest.raises(asyncio.CancelledError):
            await run_embedded_consumer(writer)

    r.xgroup_create.assert_awaited_once_with(
        STREAM_EMBEDDED, GROUP_EMBEDDED, id="0", mkstream=True,
    )


async def test_embedded_consumer_busygroup_handled():
    r = _make_mock_redis()
    r.xgroup_create.side_effect = redis.exceptions.ResponseError("BUSYGROUP Consumer Group name already exists")
    r.xreadgroup.side_effect = asyncio.CancelledError()
    writer = AsyncMock()

    with patch("app.consumer.aioredis.from_url", return_value=r):
        with pytest.raises(asyncio.CancelledError):
            await run_embedded_consumer(writer)

    # Should not raise — BUSYGROUP is swallowed
    r.xgroup_create.assert_awaited_once()


# ── Delete consumer: group creation ───────────────────────────────────────────

async def test_delete_consumer_creates_group():
    r = _make_mock_redis()
    r.xreadgroup.side_effect = asyncio.CancelledError()
    writer = AsyncMock()

    with patch("app.consumer.aioredis.from_url", return_value=r):
        with pytest.raises(asyncio.CancelledError):
            await run_delete_consumer(writer)

    r.xgroup_create.assert_awaited_once_with(
        STREAM_FILE_CHANGED, GROUP_DELETE, id="0", mkstream=True,
    )


async def test_delete_consumer_busygroup_handled():
    r = _make_mock_redis()
    r.xgroup_create.side_effect = redis.exceptions.ResponseError("BUSYGROUP Consumer Group name already exists")
    r.xreadgroup.side_effect = asyncio.CancelledError()
    writer = AsyncMock()

    with patch("app.consumer.aioredis.from_url", return_value=r):
        with pytest.raises(asyncio.CancelledError):
            await run_delete_consumer(writer)

    r.xgroup_create.assert_awaited_once()


# ── Embedded consumer: process + ACK valid messages ──────────────────────────

async def test_embedded_consumer_processes_and_acks_valid_message():
    r = _make_mock_redis()
    msg_id = b"1-0"
    data = {b"event": _make_embedded_event_json().encode()}
    r.xreadgroup.side_effect = _xreadgroup_then_cancel(
        STREAM_EMBEDDED, [(msg_id, data)],
    )
    writer = AsyncMock()

    with patch("app.consumer.aioredis.from_url", return_value=r):
        with pytest.raises(asyncio.CancelledError):
            await run_embedded_consumer(writer)

    writer.upsert_points.assert_awaited_once()
    r.xack.assert_awaited_with(STREAM_EMBEDDED, GROUP_EMBEDDED, msg_id)


# ── Delete consumer: process + ACK valid messages ────────────────────────────

async def test_delete_consumer_processes_and_acks_valid_message():
    r = _make_mock_redis()
    msg_id = b"1-0"
    data = {b"event": _make_delete_event_json().encode()}
    r.xreadgroup.side_effect = _xreadgroup_then_cancel(
        STREAM_FILE_CHANGED, [(msg_id, data)],
    )
    writer = AsyncMock()

    with patch("app.consumer.aioredis.from_url", return_value=r):
        with pytest.raises(asyncio.CancelledError):
            await run_delete_consumer(writer)

    writer.delete_points.assert_awaited_once_with(["sha256:abc", "sha256:def"])
    r.xack.assert_awaited_with(STREAM_FILE_CHANGED, GROUP_DELETE, msg_id)


# ── Bad messages → XACK (skip) ──────────────────────────────────────────────

async def test_embedded_consumer_acks_bad_json_message():
    """ValidationError / ValueError / KeyError → message is XACK'd and skipped."""
    r = _make_mock_redis()
    msg_id = b"1-0"
    data = {b"event": b"not valid json"}
    r.xreadgroup.side_effect = _xreadgroup_then_cancel(
        STREAM_EMBEDDED, [(msg_id, data)],
    )
    writer = AsyncMock()

    with patch("app.consumer.aioredis.from_url", return_value=r):
        with pytest.raises(asyncio.CancelledError):
            await run_embedded_consumer(writer)

    writer.upsert_points.assert_not_awaited()
    r.xack.assert_awaited_with(STREAM_EMBEDDED, GROUP_EMBEDDED, msg_id)


async def test_embedded_consumer_acks_missing_event_key():
    """KeyError when 'event' key missing → XACK."""
    r = _make_mock_redis()
    msg_id = b"1-0"
    data = {b"wrong_key": b"some data"}
    r.xreadgroup.side_effect = _xreadgroup_then_cancel(
        STREAM_EMBEDDED, [(msg_id, data)],
    )
    writer = AsyncMock()

    with patch("app.consumer.aioredis.from_url", return_value=r):
        with pytest.raises(asyncio.CancelledError):
            await run_embedded_consumer(writer)

    writer.upsert_points.assert_not_awaited()
    r.xack.assert_awaited_with(STREAM_EMBEDDED, GROUP_EMBEDDED, msg_id)


async def test_delete_consumer_acks_bad_json_message():
    r = _make_mock_redis()
    msg_id = b"1-0"
    data = {b"event": b"{{invalid}}"}
    r.xreadgroup.side_effect = _xreadgroup_then_cancel(
        STREAM_FILE_CHANGED, [(msg_id, data)],
    )
    writer = AsyncMock()

    with patch("app.consumer.aioredis.from_url", return_value=r):
        with pytest.raises(asyncio.CancelledError):
            await run_delete_consumer(writer)

    writer.delete_points.assert_not_awaited()
    r.xack.assert_awaited_with(STREAM_FILE_CHANGED, GROUP_DELETE, msg_id)


# ── Transient exception → no XACK (retry) ───────────────────────────────────

async def test_embedded_consumer_no_ack_on_transient_error():
    """Qdrant failure (transient) → no XACK so message will be retried."""
    r = _make_mock_redis()
    msg_id = b"1-0"
    data = {b"event": _make_embedded_event_json().encode()}

    call_count = 0

    async def xreadgroup_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [(STREAM_EMBEDDED, [(msg_id, data)])]
        raise asyncio.CancelledError()

    r.xreadgroup.side_effect = xreadgroup_side_effect
    writer = AsyncMock()
    writer.upsert_points.side_effect = Exception("qdrant timeout")

    with patch("app.consumer.aioredis.from_url", return_value=r):
        with pytest.raises(asyncio.CancelledError):
            await run_embedded_consumer(writer)

    writer.upsert_points.assert_awaited_once()
    r.xack.assert_not_awaited()


async def test_delete_consumer_no_ack_on_transient_error():
    """Qdrant failure (transient) → no XACK so message will be retried."""
    r = _make_mock_redis()
    msg_id = b"1-0"
    data = {b"event": _make_delete_event_json().encode()}

    call_count = 0

    async def xreadgroup_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [(STREAM_FILE_CHANGED, [(msg_id, data)])]
        raise asyncio.CancelledError()

    r.xreadgroup.side_effect = xreadgroup_side_effect
    writer = AsyncMock()
    writer.delete_points.side_effect = Exception("qdrant timeout")

    with patch("app.consumer.aioredis.from_url", return_value=r):
        with pytest.raises(asyncio.CancelledError):
            await run_delete_consumer(writer)

    writer.delete_points.assert_awaited_once()
    r.xack.assert_not_awaited()
