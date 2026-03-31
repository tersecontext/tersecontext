"""Integration tests for the spec-generator consumer loop (run_consumer).

These test the full consumer loop behaviour: group creation, message processing,
ACK logic, and error handling — all with mocked Redis and store.
"""
import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis.exceptions

os.environ.setdefault("POSTGRES_DSN", "postgres://test:test@localhost/test")

from app.consumer import run_consumer, _process, STREAM_IN, GROUP


def _make_path_dict(**overrides):
    base = {
        "entrypoint_stable_id": "sha256:fn_login",
        "commit_sha": "abc123",
        "repo": "acme",
        "call_sequence": [
            {
                "stable_id": "sha256:fn_login",
                "name": "login",
                "qualified_name": "auth.login",
                "hop": 0,
                "frequency_ratio": 1.0,
                "avg_ms": 10.0,
            }
        ],
        "side_effects": [],
        "dynamic_only_edges": [],
        "never_observed_static_edges": [],
        "timing_p50_ms": 10.0,
        "timing_p99_ms": 40.0,
    }
    base.update(overrides)
    return base


def _make_mock_redis(messages=None, busygroup=False):
    """Create a mock Redis that yields `messages` once then cancels."""
    r = AsyncMock()

    if busygroup:
        exc = redis.exceptions.ResponseError("BUSYGROUP Consumer Group name already exists")
        r.xgroup_create = AsyncMock(side_effect=exc)
    else:
        r.xgroup_create = AsyncMock()

    # xreadgroup: return messages on first call, then raise CancelledError to stop the loop
    call_count = 0

    async def _xreadgroup(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1 and messages is not None:
            return messages
        # Stop the consumer loop
        raise asyncio.CancelledError()

    r.xreadgroup = AsyncMock(side_effect=_xreadgroup)
    r.xack = AsyncMock()
    r.aclose = AsyncMock()
    return r


# ── Group creation ──────────────────────────────────────────────────────────


async def test_consumer_creates_group_on_startup():
    """Consumer calls xgroup_create with mkstream=True on startup."""
    mock_redis = _make_mock_redis()

    with patch("app.consumer.aioredis.from_url", return_value=mock_redis):
        mock_store = MagicMock()
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(mock_store)

    mock_redis.xgroup_create.assert_called_once_with(
        STREAM_IN, GROUP, id="0", mkstream=True
    )


async def test_consumer_tolerates_existing_group():
    """Consumer handles BUSYGROUP error gracefully (group already exists)."""
    mock_redis = _make_mock_redis(busygroup=True)

    with patch("app.consumer.aioredis.from_url", return_value=mock_redis):
        mock_store = MagicMock()
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(mock_store)

    # Should not raise — BUSYGROUP is caught silently
    mock_redis.xgroup_create.assert_called_once()


# ── Message processing ──────────────────────────────────────────────────────


async def test_consumer_processes_execution_path_message():
    """Consumer processes a valid ExecutionPath message and ACKs it."""
    import app.consumer as consumer

    consumer.messages_processed_total = 0
    consumer.specs_written_total = 0
    consumer.specs_embedded_total = 0

    msg_id = b"1-0"
    event_data = {b"event": json.dumps(_make_path_dict()).encode()}
    messages = [(STREAM_IN.encode(), [(msg_id, event_data)])]

    mock_redis = _make_mock_redis(messages=messages)

    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock()
    mock_store.upsert_qdrant = AsyncMock()

    with patch("app.consumer.aioredis.from_url", return_value=mock_redis):
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(mock_store)

    mock_store.upsert_spec.assert_called_once()
    mock_store.upsert_qdrant.assert_called_once()
    mock_redis.xack.assert_called_once_with(STREAM_IN, GROUP, msg_id)
    assert consumer.messages_processed_total == 1
    assert consumer.specs_written_total == 1
    assert consumer.specs_embedded_total == 1


async def test_consumer_full_flow_render_store_embed():
    """Full flow: message in → render spec → store in postgres → embed in qdrant."""
    import app.consumer as consumer

    consumer.specs_written_total = 0
    consumer.specs_embedded_total = 0

    path_dict = _make_path_dict(
        call_sequence=[
            {
                "stable_id": "sha256:fn_auth",
                "name": "authenticate",
                "qualified_name": "auth.service.authenticate",
                "hop": 0,
                "frequency_ratio": 1.0,
                "avg_ms": 28.0,
            },
            {
                "stable_id": "sha256:fn_validate",
                "name": "validate_token",
                "qualified_name": "auth.service.validate_token",
                "hop": 1,
                "frequency_ratio": 0.8,
                "avg_ms": 5.0,
            },
        ],
        side_effects=[
            {"type": "db_read", "detail": "SELECT * FROM users WHERE id = $1", "hop_depth": 1},
        ],
    )

    msg_id = b"2-0"
    event_data = {"event": json.dumps(path_dict)}
    messages = [(STREAM_IN, [(msg_id, event_data)])]

    mock_redis = _make_mock_redis(messages=messages)

    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock()
    mock_store.upsert_qdrant = AsyncMock()

    with patch("app.consumer.aioredis.from_url", return_value=mock_redis):
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(mock_store)

    # Verify spec was rendered correctly
    spec_call_args = mock_store.upsert_spec.call_args[0]
    path_obj = spec_call_args[0]
    spec_text = spec_call_args[1]
    assert path_obj.entrypoint_stable_id == "sha256:fn_login"
    assert "authenticate" in spec_text
    assert "validate_token" in spec_text
    assert "DB READ" in spec_text
    assert "users" in spec_text

    # Verify qdrant embedding was called with correct entrypoint name
    qdrant_call_args = mock_store.upsert_qdrant.call_args[0]
    assert qdrant_call_args[1] == "authenticate"  # entrypoint_name from first call_sequence item

    assert consumer.specs_written_total == 1
    assert consumer.specs_embedded_total == 1


# ── ACK behaviour ───────────────────────────────────────────────────────────


async def test_consumer_acks_on_success():
    """Consumer ACKs message after successful processing."""
    msg_id = b"3-0"
    event_data = {"event": json.dumps(_make_path_dict())}
    messages = [(STREAM_IN, [(msg_id, event_data)])]

    mock_redis = _make_mock_redis(messages=messages)
    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock()
    mock_store.upsert_qdrant = AsyncMock()

    with patch("app.consumer.aioredis.from_url", return_value=mock_redis):
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(mock_store)

    mock_redis.xack.assert_called_once_with(STREAM_IN, GROUP, msg_id)


# ── Error handling ──────────────────────────────────────────────────────────


async def test_consumer_handles_malformed_message_and_acks():
    """Consumer ACKs malformed messages (bad JSON) and increments failed counter."""
    import app.consumer as consumer

    consumer.messages_processed_total = 0
    consumer.messages_failed_total = 0

    msg_id = b"4-0"
    event_data = {"event": "not valid json {{{"}
    messages = [(STREAM_IN, [(msg_id, event_data)])]

    mock_redis = _make_mock_redis(messages=messages)
    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock()
    mock_store.upsert_qdrant = AsyncMock()

    with patch("app.consumer.aioredis.from_url", return_value=mock_redis):
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(mock_store)

    # Malformed messages are ACKed (skipped)
    mock_redis.xack.assert_called_once_with(STREAM_IN, GROUP, msg_id)
    assert consumer.messages_failed_total == 1
    assert consumer.messages_processed_total == 0
    mock_store.upsert_spec.assert_not_called()


async def test_consumer_handles_validation_error_and_acks():
    """Consumer ACKs messages that fail Pydantic validation."""
    import app.consumer as consumer

    consumer.messages_failed_total = 0

    msg_id = b"5-0"
    event_data = {"event": json.dumps({"bad": "schema"})}
    messages = [(STREAM_IN, [(msg_id, event_data)])]

    mock_redis = _make_mock_redis(messages=messages)
    mock_store = MagicMock()

    with patch("app.consumer.aioredis.from_url", return_value=mock_redis):
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(mock_store)

    mock_redis.xack.assert_called_once_with(STREAM_IN, GROUP, msg_id)
    assert consumer.messages_failed_total == 1


async def test_consumer_handles_store_failure_does_not_ack():
    """Consumer does NOT ACK messages when store raises a non-validation error (retry)."""
    import app.consumer as consumer

    consumer.messages_processed_total = 0
    consumer.messages_failed_total = 0

    msg_id = b"6-0"
    event_data = {"event": json.dumps(_make_path_dict())}
    messages = [(STREAM_IN, [(msg_id, event_data)])]

    mock_redis = _make_mock_redis(messages=messages)
    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock(side_effect=Exception("postgres down"))
    mock_store.upsert_qdrant = AsyncMock()

    with patch("app.consumer.aioredis.from_url", return_value=mock_redis):
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(mock_store)

    # Store failure is a generic Exception — not ACKed, will be retried
    mock_redis.xack.assert_not_called()
    assert consumer.messages_processed_total == 0


async def test_consumer_handles_missing_event_key_and_acks():
    """Consumer ACKs messages with missing 'event' key (KeyError)."""
    import app.consumer as consumer

    consumer.messages_failed_total = 0

    msg_id = b"7-0"
    event_data = {"wrong_key": "data"}
    messages = [(STREAM_IN, [(msg_id, event_data)])]

    mock_redis = _make_mock_redis(messages=messages)
    mock_store = MagicMock()

    with patch("app.consumer.aioredis.from_url", return_value=mock_redis):
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(mock_store)

    mock_redis.xack.assert_called_once_with(STREAM_IN, GROUP, msg_id)
    assert consumer.messages_failed_total == 1
