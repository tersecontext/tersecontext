# services/graph-writer/tests/test_consumer.py
import asyncio
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.consumer import (
    GROUP_EDGES,
    GROUP_NODES,
    STREAM_EMBEDDED,
    STREAM_PARSED,
    _parsed_file_cache,
    run_edge_consumer,
    run_node_consumer,
)
from app.models import (
    EmbeddedNode,
    EmbeddedNodesEvent,
    IntraFileEdge,
    ParsedFileEvent,
    ParsedNode,
)


# ── Helpers ──


def _make_parsed_file_event(**overrides) -> ParsedFileEvent:
    defaults = dict(
        repo="test",
        commit_sha="abc123",
        file_path="src/foo.py",
        language="python",
        nodes=[
            ParsedNode(
                stable_id="sid1",
                node_hash="hash1",
                type="function",
                name="my_func",
                qualified_name="my_func",
                signature="my_func()",
                docstring="",
                body="def my_func(): pass",
                line_start=1,
                line_end=1,
            ),
        ],
        intra_file_edges=[
            IntraFileEdge(
                source_stable_id="sid1",
                target_stable_id="sid2",
                type="CALLS",
            ),
        ],
        deleted_nodes=[],
    )
    defaults.update(overrides)
    return ParsedFileEvent(**defaults)


def _make_embedded_nodes_event(**overrides) -> EmbeddedNodesEvent:
    defaults = dict(
        repo="test",
        commit_sha="abc123",
        file_path="src/foo.py",
        nodes=[
            EmbeddedNode(
                stable_id="sid1",
                vector=[0.1, 0.2],
                embed_text="my_func my_func()",
                node_hash="hash1",
            ),
        ],
    )
    defaults.update(overrides)
    return EmbeddedNodesEvent(**defaults)


def _make_redis_message(event_json: str, msg_id: bytes = b"1-0"):
    """Return the structure that xreadgroup yields: [(stream, [(msg_id, data)])]"""
    return [(b"stream", [(msg_id, {b"event": event_json.encode()})])]


async def _run_consumer_once(consumer_coro, driver, messages, *, cancel_after: int = 1):
    """Run a consumer coroutine, feeding it `messages` on first call,
    then raising CancelledError to break the loop.
    Returns the mock redis client so callers can assert on it."""
    call_count = 0

    async def fake_xreadgroup(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return messages
        # Cancel the task after first batch is processed
        raise asyncio.CancelledError()

    mock_redis = AsyncMock()
    mock_redis.xreadgroup = fake_xreadgroup
    # xgroup_create succeeds
    mock_redis.xgroup_create = AsyncMock()
    mock_redis.xack = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with patch("app.consumer.aioredis") as mock_aioredis:
        mock_aioredis.from_url.return_value = mock_redis
        with pytest.raises(asyncio.CancelledError):
            await consumer_coro(driver)

    return mock_redis


# ── Edge consumer tests ──


@pytest.mark.asyncio
async def test_edge_consumer_processes_parsed_file_events():
    """Edge consumer parses a ParsedFileEvent, calls upsert_edges, and ACKs."""
    event = _make_parsed_file_event()
    messages = _make_redis_message(event.model_dump_json())
    driver = MagicMock()

    with patch("app.consumer.writer") as mock_writer:
        mock_redis = await _run_consumer_once(run_edge_consumer, driver, messages)

    mock_writer.upsert_edges.assert_called_once()
    edges_arg = mock_writer.upsert_edges.call_args[0][1]
    assert edges_arg == [{"source": "sid1", "target": "sid2"}]
    mock_redis.xack.assert_called_once()


@pytest.mark.asyncio
async def test_edge_consumer_calls_tombstone_for_deleted_nodes():
    """Edge consumer calls tombstone when deleted_nodes is non-empty."""
    event = _make_parsed_file_event(deleted_nodes=["sid_old"])
    messages = _make_redis_message(event.model_dump_json())
    driver = MagicMock()

    with patch("app.consumer.writer") as mock_writer:
        mock_redis = await _run_consumer_once(run_edge_consumer, driver, messages)

    mock_writer.tombstone.assert_called_once()
    assert mock_writer.tombstone.call_args[0][1] == ["sid_old"]


@pytest.mark.asyncio
async def test_edge_consumer_populates_parsed_file_cache():
    """Edge consumer stores events in _parsed_file_cache for node consumer."""
    event = _make_parsed_file_event(commit_sha="sha1", file_path="a/b.py")
    messages = _make_redis_message(event.model_dump_json())
    driver = MagicMock()

    # Clear cache before test
    _parsed_file_cache.clear()

    with patch("app.consumer.writer"):
        await _run_consumer_once(run_edge_consumer, driver, messages)

    assert ("sha1", "a/b.py") in _parsed_file_cache
    cached = _parsed_file_cache[("sha1", "a/b.py")]
    assert cached.repo == "test"

    # Cleanup
    _parsed_file_cache.clear()


@pytest.mark.asyncio
async def test_edge_consumer_creates_group_on_startup():
    """Edge consumer calls xgroup_create on startup."""
    messages = []  # No messages, will cancel immediately
    driver = MagicMock()

    call_count = 0

    async def fake_xreadgroup(**kwargs):
        nonlocal call_count
        call_count += 1
        raise asyncio.CancelledError()

    mock_redis = AsyncMock()
    mock_redis.xreadgroup = fake_xreadgroup
    mock_redis.xgroup_create = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with patch("app.consumer.aioredis") as mock_aioredis:
        mock_aioredis.from_url.return_value = mock_redis
        with pytest.raises(asyncio.CancelledError):
            await run_edge_consumer(driver)

    mock_redis.xgroup_create.assert_awaited_once_with(
        STREAM_PARSED, GROUP_EDGES, id="0", mkstream=True
    )


@pytest.mark.asyncio
async def test_edge_consumer_acks_after_successful_processing():
    """Edge consumer calls XACK with correct stream, group, and msg_id."""
    event = _make_parsed_file_event()
    msg_id = b"42-1"
    messages = [(b"stream", [(msg_id, {b"event": event.model_dump_json().encode()})])]
    driver = MagicMock()

    with patch("app.consumer.writer"):
        mock_redis = await _run_consumer_once(run_edge_consumer, driver, messages)

    mock_redis.xack.assert_awaited_once_with(STREAM_PARSED, GROUP_EDGES, msg_id)


@pytest.mark.asyncio
async def test_edge_consumer_handles_malformed_message():
    """Malformed JSON is ACKed (skipped) without crashing the consumer."""
    messages = [(b"stream", [(b"1-0", {b"event": b"not valid json"})])]
    driver = MagicMock()

    with patch("app.consumer.writer") as mock_writer:
        mock_redis = await _run_consumer_once(run_edge_consumer, driver, messages)

    mock_writer.upsert_edges.assert_not_called()
    # Malformed messages are still ACKed (see consumer.py lines 74-76)
    mock_redis.xack.assert_awaited_once()


@pytest.mark.asyncio
async def test_edge_consumer_handles_missing_event_key():
    """Message without 'event' key is ACKed without crashing."""
    messages = [(b"stream", [(b"1-0", {b"other": b"data"})])]
    driver = MagicMock()

    with patch("app.consumer.writer") as mock_writer:
        mock_redis = await _run_consumer_once(run_edge_consumer, driver, messages)

    mock_writer.upsert_edges.assert_not_called()
    mock_redis.xack.assert_awaited_once()


@pytest.mark.asyncio
async def test_edge_consumer_transient_error_no_ack():
    """Transient errors (e.g. Neo4j down) do NOT ack the message."""
    event = _make_parsed_file_event()
    messages = _make_redis_message(event.model_dump_json())
    driver = MagicMock()

    with patch("app.consumer.writer") as mock_writer:
        mock_writer.upsert_edges.side_effect = RuntimeError("neo4j timeout")
        mock_redis = await _run_consumer_once(run_edge_consumer, driver, messages)

    # Transient error -> no XACK (message will be redelivered)
    mock_redis.xack.assert_not_awaited()


# ── Node consumer tests ──


@pytest.mark.asyncio
async def test_node_consumer_processes_embedded_nodes_events():
    """Node consumer uses cache + embedded event to upsert nodes."""
    parsed_event = _make_parsed_file_event(commit_sha="sha1", file_path="src/foo.py")
    embedded_event = _make_embedded_nodes_event(commit_sha="sha1", file_path="src/foo.py")

    # Pre-populate cache (edge consumer would have done this)
    _parsed_file_cache.clear()
    _parsed_file_cache[("sha1", "src/foo.py")] = parsed_event

    messages = _make_redis_message(embedded_event.model_dump_json())
    driver = MagicMock()

    with patch("app.consumer.writer") as mock_writer:
        mock_writer.build_node_records.return_value = [{"stable_id": "sid1"}]
        mock_redis = await _run_consumer_once(run_node_consumer, driver, messages)

    mock_writer.build_node_records.assert_called_once()
    mock_writer.upsert_nodes.assert_called_once()
    mock_redis.xack.assert_awaited_once()

    _parsed_file_cache.clear()


@pytest.mark.asyncio
async def test_node_consumer_cache_miss_skips_and_acks():
    """Node consumer skips processing on cache miss but still ACKs."""
    embedded_event = _make_embedded_nodes_event(commit_sha="miss", file_path="nope.py")
    _parsed_file_cache.clear()

    messages = _make_redis_message(embedded_event.model_dump_json())
    driver = MagicMock()

    with patch("app.consumer.writer") as mock_writer:
        mock_redis = await _run_consumer_once(run_node_consumer, driver, messages)

    mock_writer.upsert_nodes.assert_not_called()
    mock_redis.xack.assert_awaited_once()

    _parsed_file_cache.clear()


@pytest.mark.asyncio
async def test_node_consumer_creates_group_on_startup():
    """Node consumer calls xgroup_create on startup."""
    driver = MagicMock()

    async def fake_xreadgroup(**kwargs):
        raise asyncio.CancelledError()

    mock_redis = AsyncMock()
    mock_redis.xreadgroup = fake_xreadgroup
    mock_redis.xgroup_create = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with patch("app.consumer.aioredis") as mock_aioredis:
        mock_aioredis.from_url.return_value = mock_redis
        with pytest.raises(asyncio.CancelledError):
            await run_node_consumer(driver)

    mock_redis.xgroup_create.assert_awaited_once_with(
        STREAM_EMBEDDED, GROUP_NODES, id="0", mkstream=True
    )


@pytest.mark.asyncio
async def test_node_consumer_handles_malformed_message():
    """Malformed JSON is ACKed without crashing."""
    messages = [(b"stream", [(b"1-0", {b"event": b"{{bad json"})])]
    driver = MagicMock()
    _parsed_file_cache.clear()

    with patch("app.consumer.writer") as mock_writer:
        mock_redis = await _run_consumer_once(run_node_consumer, driver, messages)

    mock_writer.upsert_nodes.assert_not_called()
    mock_redis.xack.assert_awaited_once()

    _parsed_file_cache.clear()


@pytest.mark.asyncio
async def test_node_consumer_transient_error_no_ack():
    """Transient errors do NOT ack the message."""
    parsed_event = _make_parsed_file_event(commit_sha="sha1", file_path="src/foo.py")
    embedded_event = _make_embedded_nodes_event(commit_sha="sha1", file_path="src/foo.py")

    _parsed_file_cache.clear()
    _parsed_file_cache[("sha1", "src/foo.py")] = parsed_event

    messages = _make_redis_message(embedded_event.model_dump_json())
    driver = MagicMock()

    with patch("app.consumer.writer") as mock_writer:
        mock_writer.build_node_records.return_value = [{"stable_id": "sid1"}]
        mock_writer.upsert_nodes.side_effect = RuntimeError("neo4j timeout")
        mock_redis = await _run_consumer_once(run_node_consumer, driver, messages)

    mock_redis.xack.assert_not_awaited()

    _parsed_file_cache.clear()


@pytest.mark.asyncio
async def test_cache_coordination_edge_then_node():
    """End-to-end: edge consumer populates cache, node consumer reads it."""
    parsed_event = _make_parsed_file_event(commit_sha="coordsha", file_path="lib/x.py")
    embedded_event = _make_embedded_nodes_event(commit_sha="coordsha", file_path="lib/x.py")

    _parsed_file_cache.clear()
    driver = MagicMock()

    # Step 1: edge consumer processes parsed-file event
    edge_messages = _make_redis_message(parsed_event.model_dump_json())
    with patch("app.consumer.writer"):
        await _run_consumer_once(run_edge_consumer, driver, edge_messages)

    # Verify cache was populated
    assert ("coordsha", "lib/x.py") in _parsed_file_cache

    # Step 2: node consumer processes embedded-nodes event using the cache
    node_messages = _make_redis_message(embedded_event.model_dump_json())
    with patch("app.consumer.writer") as mock_writer:
        mock_writer.build_node_records.return_value = [{"stable_id": "sid1"}]
        await _run_consumer_once(run_node_consumer, driver, node_messages)

    mock_writer.upsert_nodes.assert_called_once()

    _parsed_file_cache.clear()
