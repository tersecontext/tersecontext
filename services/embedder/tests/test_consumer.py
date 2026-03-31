"""Integration tests for embedder Redis stream consumer (app/consumer.py)."""
import asyncio
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.consumer import _process, run_consumer, STREAM_IN, STREAM_OUT, GROUP
from app.models import ParsedFileEvent, ParsedNode


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_node(name: str, node_hash: str = "sha256:new") -> ParsedNode:
    return ParsedNode(
        stable_id=f"sha256:{name}",
        node_hash=node_hash,
        type="function",
        name=name,
        signature=f"{name}()",
        docstring="",
        body="pass",
        line_start=1,
        line_end=1,
    )


def _make_event(nodes=None) -> ParsedFileEvent:
    if nodes is None:
        nodes = [_make_node("my_func")]
    return ParsedFileEvent(
        repo="acme",
        commit_sha="abc123",
        file_path="src/foo.py",
        language="python",
        nodes=nodes,
        intra_file_edges=[],
    )


def _fake_provider():
    provider = MagicMock()

    async def fake_embed(texts):
        return [[0.1] * 768 for _ in texts]

    provider.embed = fake_embed
    return provider


def _fake_neo4j(hashes=None):
    client = AsyncMock()
    client.get_node_hashes.return_value = hashes or {}
    return client


# ── _process tests ───────────────────────────────────────────────────────────


async def test_process_embeds_and_emits_to_output_stream():
    """Full message flow: ParsedFileEvent -> embed -> xadd to output stream."""
    event = _make_event()
    mock_r = AsyncMock()
    provider = _fake_provider()
    neo4j = _fake_neo4j()

    await _process(
        mock_r,
        {"event": event.model_dump_json()},
        provider,
        neo4j,
        batch_size=64,
        embedding_dim=0,
    )

    mock_r.xadd.assert_called_once()
    call_args = mock_r.xadd.call_args
    assert call_args[0][0] == STREAM_OUT
    payload = json.loads(call_args[0][1]["event"])
    assert payload["repo"] == "acme"
    assert payload["file_path"] == "src/foo.py"
    assert len(payload["nodes"]) == 1
    assert payload["nodes"][0]["stable_id"] == "sha256:my_func"


async def test_process_handles_bytes_event_key():
    """Redis may return keys as bytes."""
    event = _make_event()
    mock_r = AsyncMock()
    provider = _fake_provider()
    neo4j = _fake_neo4j()

    await _process(
        mock_r,
        {b"event": event.model_dump_json().encode("utf-8")},
        provider,
        neo4j,
        batch_size=64,
        embedding_dim=0,
    )

    mock_r.xadd.assert_called_once()


async def test_process_raises_on_missing_event_key():
    """Message without 'event' key should raise KeyError."""
    mock_r = AsyncMock()
    provider = _fake_provider()
    neo4j = _fake_neo4j()

    with pytest.raises(KeyError, match="event"):
        await _process(
            mock_r,
            {"wrong_key": "data"},
            provider,
            neo4j,
            batch_size=64,
            embedding_dim=0,
        )


async def test_process_raises_on_malformed_json():
    """Malformed JSON in 'event' should raise."""
    mock_r = AsyncMock()
    provider = _fake_provider()
    neo4j = _fake_neo4j()

    with pytest.raises(Exception):
        await _process(
            mock_r,
            {"event": "not valid json {{{"},
            provider,
            neo4j,
            batch_size=64,
            embedding_dim=0,
        )


async def test_process_handles_neo4j_failure_gracefully():
    """When neo4j raises, all nodes are still embedded."""
    nodes = [_make_node("fn1"), _make_node("fn2")]
    event = _make_event(nodes=nodes)
    mock_r = AsyncMock()
    provider = _fake_provider()
    neo4j = AsyncMock()
    neo4j.get_node_hashes.side_effect = Exception("neo4j down")

    await _process(
        mock_r,
        {"event": event.model_dump_json()},
        provider,
        neo4j,
        batch_size=64,
        embedding_dim=0,
    )

    mock_r.xadd.assert_called_once()
    payload = json.loads(mock_r.xadd.call_args[0][1]["event"])
    assert len(payload["nodes"]) == 2


async def test_process_skips_unchanged_nodes():
    """Nodes with matching hash in neo4j cache are skipped."""
    node = _make_node("fn", node_hash="sha256:existing")
    event = _make_event(nodes=[node])
    mock_r = AsyncMock()
    provider = _fake_provider()
    neo4j = _fake_neo4j(hashes={"sha256:fn": "sha256:existing"})

    await _process(
        mock_r,
        {"event": event.model_dump_json()},
        provider,
        neo4j,
        batch_size=64,
        embedding_dim=0,
    )

    mock_r.xadd.assert_called_once()
    payload = json.loads(mock_r.xadd.call_args[0][1]["event"])
    assert len(payload["nodes"]) == 0


# ── run_consumer tests ───────────────────────────────────────────────────────


async def test_consumer_creates_group_on_startup():
    """Consumer should call xgroup_create on startup."""
    mock_r = AsyncMock()
    mock_r.xgroup_create.return_value = True
    mock_r.xreadgroup.side_effect = asyncio.CancelledError

    provider = _fake_provider()
    neo4j = _fake_neo4j()

    with patch("app.consumer.aioredis.from_url", return_value=mock_r):
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(provider, neo4j, batch_size=64, embedding_dim=768)

    mock_r.xgroup_create.assert_called_once_with(
        STREAM_IN, GROUP, id="0", mkstream=True
    )


async def test_consumer_ignores_busygroup_error():
    """If consumer group already exists (BUSYGROUP), consumer continues."""
    import redis.exceptions

    mock_r = AsyncMock()
    mock_r.xgroup_create.side_effect = redis.exceptions.ResponseError(
        "BUSYGROUP Consumer Group name already exists"
    )
    mock_r.xreadgroup.side_effect = asyncio.CancelledError

    provider = _fake_provider()
    neo4j = _fake_neo4j()

    with patch("app.consumer.aioredis.from_url", return_value=mock_r):
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(provider, neo4j, batch_size=64, embedding_dim=768)

    # Should not have raised — the BUSYGROUP error is handled
    mock_r.xreadgroup.assert_called()


async def test_consumer_acks_after_successful_processing():
    """Consumer should XACK messages after successful processing."""
    event = _make_event()
    msg_id = b"1-0"
    mock_r = AsyncMock()
    mock_r.xgroup_create.return_value = True

    call_count = 0

    async def xreadgroup_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [(STREAM_IN, [(msg_id, {b"event": event.model_dump_json().encode()})])]
        raise asyncio.CancelledError

    mock_r.xreadgroup.side_effect = xreadgroup_side_effect

    provider = _fake_provider()
    neo4j = _fake_neo4j()

    with patch("app.consumer.aioredis.from_url", return_value=mock_r):
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(provider, neo4j, batch_size=64, embedding_dim=768)

    mock_r.xack.assert_called_once_with(STREAM_IN, GROUP, msg_id)


async def test_consumer_acks_malformed_messages():
    """Malformed messages should still be ACKed (so they don't block the group)."""
    msg_id = b"1-0"
    mock_r = AsyncMock()
    mock_r.xgroup_create.return_value = True

    call_count = 0

    async def xreadgroup_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [(STREAM_IN, [(msg_id, {b"event": b"not valid json"})])]
        raise asyncio.CancelledError

    mock_r.xreadgroup.side_effect = xreadgroup_side_effect

    provider = _fake_provider()
    neo4j = _fake_neo4j()

    with patch("app.consumer.aioredis.from_url", return_value=mock_r):
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(provider, neo4j, batch_size=64, embedding_dim=768)

    mock_r.xack.assert_called_once_with(STREAM_IN, GROUP, msg_id)


async def test_consumer_handles_embedding_failure_without_ack():
    """When embedding fails (non-validation error), message is NOT acked."""
    event = _make_event()
    msg_id = b"1-0"
    mock_r = AsyncMock()
    mock_r.xgroup_create.return_value = True

    call_count = 0

    async def xreadgroup_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [(STREAM_IN, [(msg_id, {b"event": event.model_dump_json().encode()})])]
        raise asyncio.CancelledError

    mock_r.xreadgroup.side_effect = xreadgroup_side_effect

    # Provider that blows up
    bad_provider = MagicMock()

    async def exploding_embed(texts):
        raise RuntimeError("GPU on fire")

    bad_provider.embed = exploding_embed
    neo4j = _fake_neo4j()

    with patch("app.consumer.aioredis.from_url", return_value=mock_r):
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(bad_provider, neo4j, batch_size=64, embedding_dim=768)

    # Message should NOT be acked since embedding failed with RuntimeError
    mock_r.xack.assert_not_called()


async def test_consumer_closes_redis_on_cancel():
    """Consumer should close Redis connection in finally block."""
    mock_r = AsyncMock()
    mock_r.xgroup_create.return_value = True
    mock_r.xreadgroup.side_effect = asyncio.CancelledError

    provider = _fake_provider()
    neo4j = _fake_neo4j()

    with patch("app.consumer.aioredis.from_url", return_value=mock_r):
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(provider, neo4j, batch_size=64, embedding_dim=768)

    mock_r.aclose.assert_called_once()
