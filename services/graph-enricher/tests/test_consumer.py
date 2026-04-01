# services/graph-enricher/tests/test_consumer.py
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
import redis.exceptions

from app.consumer import _process_event, run_consumer, STREAM, GROUP
from app.models import ExecutionPath, CallNode, SideEffect, DynamicEdge


def _make_execution_path(**overrides):
    """Build a minimal ExecutionPath for testing."""
    defaults = {
        "entrypoint_stable_id": "sha256:fn_test_login",
        "commit_sha": "a4f91c",
        "call_sequence": [
            CallNode(stable_id="sha256:fn_authenticate", hop=1, frequency_ratio=1.0, avg_ms=28.0),
            CallNode(stable_id="sha256:fn_validate", hop=2, frequency_ratio=0.8, avg_ms=10.0),
        ],
        "side_effects": [
            SideEffect(type="db_read", detail="SELECT * FROM users", hop_depth=1),
        ],
        "dynamic_only_edges": [
            DynamicEdge(source="sha256:fn_authenticate", target="sha256:fn_audit_log"),
        ],
        "never_observed_static_edges": [],
        "timing_p50_ms": 50.0,
        "timing_p99_ms": 120.0,
    }
    defaults.update(overrides)
    return ExecutionPath(**defaults)


def _make_driver():
    driver = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=MagicMock())
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    return driver


# ── _process_event tests ──


def test_process_event_calls_enricher_functions():
    """_process_event calls all three enricher functions with correct data."""
    driver = _make_driver()
    event = _make_execution_path()

    with patch("app.consumer.enricher") as mock_enricher:
        _process_event(driver, event)

        mock_enricher.update_node_props_batch.assert_called_once()
        mock_enricher.upsert_dynamic_edges.assert_called_once()
        mock_enricher.confirm_static_edges.assert_called_once()

        # Verify node records include all call_sequence nodes
        node_records = mock_enricher.update_node_props_batch.call_args[0][1]
        stable_ids = {r["stable_id"] for r in node_records}
        assert "sha256:fn_authenticate" in stable_ids
        assert "sha256:fn_validate" in stable_ids

        # Verify dynamic edges
        edges = mock_enricher.upsert_dynamic_edges.call_args[0][1]
        assert len(edges) == 1
        assert edges[0]["source"] == "sha256:fn_authenticate"
        assert edges[0]["target"] == "sha256:fn_audit_log"


def test_process_event_adds_entrypoint_if_not_in_sequence():
    """Entrypoint node is enriched even when it doesn't appear in call_sequence."""
    driver = _make_driver()
    event = _make_execution_path(
        entrypoint_stable_id="sha256:fn_entry",
        call_sequence=[
            CallNode(stable_id="sha256:fn_other", hop=1, frequency_ratio=1.0, avg_ms=5.0),
        ],
    )

    with patch("app.consumer.enricher") as mock_enricher:
        _process_event(driver, event)

        node_records = mock_enricher.update_node_props_batch.call_args[0][1]
        stable_ids = {r["stable_id"] for r in node_records}
        assert "sha256:fn_entry" in stable_ids
        assert "sha256:fn_other" in stable_ids


def test_process_event_entrypoint_in_observed_ids():
    """confirm_static_edges receives entrypoint even if not in call_sequence."""
    driver = _make_driver()
    event = _make_execution_path(
        entrypoint_stable_id="sha256:fn_entry",
        call_sequence=[
            CallNode(stable_id="sha256:fn_other", hop=1, frequency_ratio=1.0, avg_ms=5.0),
        ],
    )

    with patch("app.consumer.enricher") as mock_enricher:
        _process_event(driver, event)

        observed_ids = mock_enricher.confirm_static_edges.call_args[0][1]
        assert "sha256:fn_entry" in observed_ids


# ── run_consumer tests ──


@pytest.mark.asyncio
async def test_consumer_creates_group_on_startup():
    """Consumer creates the consumer group on startup."""
    mock_r = AsyncMock()
    mock_r.xgroup_create.return_value = True
    # Make xreadgroup raise CancelledError to exit loop immediately
    mock_r.xreadgroup.side_effect = asyncio.CancelledError

    driver = MagicMock()

    with patch("shared.consumer.aioredis.from_url", return_value=mock_r):
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(driver)

    mock_r.xgroup_create.assert_called_once_with(
        STREAM, GROUP, id="0", mkstream=True
    )


@pytest.mark.asyncio
async def test_consumer_ignores_busygroup_error():
    """Consumer ignores BUSYGROUP error (group already exists)."""
    mock_r = AsyncMock()
    mock_r.xgroup_create.side_effect = redis.exceptions.ResponseError("BUSYGROUP Consumer Group name already exists")
    mock_r.xreadgroup.side_effect = asyncio.CancelledError

    driver = MagicMock()

    with patch("shared.consumer.aioredis.from_url", return_value=mock_r):
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(driver)

    # Should not raise; BUSYGROUP is expected


@pytest.mark.asyncio
async def test_consumer_acks_after_successful_processing():
    """Consumer ACKs messages after successful processing."""
    event = _make_execution_path()
    event_json = event.model_dump_json()

    msg_id = b"1-0"
    messages = [
        (STREAM.encode(), [(msg_id, {b"event": event_json.encode()})])
    ]

    call_count = 0

    async def xreadgroup_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return messages
        raise asyncio.CancelledError

    mock_r = AsyncMock()
    mock_r.xgroup_create.return_value = True
    mock_r.xreadgroup.side_effect = xreadgroup_side_effect

    driver = _make_driver()

    with patch("shared.consumer.aioredis.from_url", return_value=mock_r), \
         patch("app.consumer.enricher"):
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(driver)

    mock_r.xack.assert_any_call(STREAM, GROUP, msg_id)


@pytest.mark.asyncio
async def test_consumer_handles_malformed_messages():
    """Consumer doesn't crash on messages with missing 'event' key."""
    msg_id = b"2-0"
    messages = [
        (STREAM.encode(), [(msg_id, {b"bad_key": b"not an event"})])
    ]

    call_count = 0

    async def xreadgroup_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return messages
        raise asyncio.CancelledError

    mock_r = AsyncMock()
    mock_r.xgroup_create.return_value = True
    mock_r.xreadgroup.side_effect = xreadgroup_side_effect

    driver = _make_driver()

    with patch("shared.consumer.aioredis.from_url", return_value=mock_r):
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(driver)

    # Malformed message should still be ACKed (skipped)
    mock_r.xack.assert_called_once_with(STREAM, GROUP, msg_id)


@pytest.mark.asyncio
async def test_consumer_handles_invalid_json():
    """Consumer doesn't crash on messages with invalid JSON in 'event'."""
    msg_id = b"3-0"
    messages = [
        (STREAM.encode(), [(msg_id, {b"event": b"not valid json"})])
    ]

    call_count = 0

    async def xreadgroup_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return messages
        raise asyncio.CancelledError

    mock_r = AsyncMock()
    mock_r.xgroup_create.return_value = True
    mock_r.xreadgroup.side_effect = xreadgroup_side_effect

    driver = _make_driver()

    with patch("shared.consumer.aioredis.from_url", return_value=mock_r):
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(driver)

    # Invalid JSON still ACKed
    mock_r.xack.assert_called_once_with(STREAM, GROUP, msg_id)


@pytest.mark.asyncio
async def test_consumer_runs_post_batch_operations():
    """Conflict detector and staleness downgrade run after processing events."""
    event = _make_execution_path()
    event_json = event.model_dump_json()

    msg_id = b"1-0"
    messages = [
        (STREAM.encode(), [(msg_id, {b"event": event_json.encode()})])
    ]

    call_count = 0

    async def xreadgroup_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return messages
        raise asyncio.CancelledError

    mock_r = AsyncMock()
    mock_r.xgroup_create.return_value = True
    mock_r.xreadgroup.side_effect = xreadgroup_side_effect

    driver = _make_driver()

    with patch("shared.consumer.aioredis.from_url", return_value=mock_r), \
         patch("app.consumer.enricher") as mock_enricher:
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(driver)

    mock_enricher.run_conflict_detector.assert_called_once_with(driver)
    mock_enricher.run_staleness_downgrade.assert_called_once_with(driver)


@pytest.mark.asyncio
async def test_consumer_skips_post_batch_when_no_events():
    """Post-batch operations are NOT called when no events were processed."""
    call_count = 0

    async def xreadgroup_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return []  # No messages
        raise asyncio.CancelledError

    mock_r = AsyncMock()
    mock_r.xgroup_create.return_value = True
    mock_r.xreadgroup.side_effect = xreadgroup_side_effect

    driver = _make_driver()

    with patch("shared.consumer.aioredis.from_url", return_value=mock_r), \
         patch("app.consumer.enricher") as mock_enricher:
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(driver)

    mock_enricher.run_conflict_detector.assert_not_called()
    mock_enricher.run_staleness_downgrade.assert_not_called()


@pytest.mark.asyncio
async def test_consumer_handles_processing_error_gracefully():
    """Consumer continues running when post_batch raises an exception; messages are ACKed."""
    event = _make_execution_path()
    event_json = event.model_dump_json()

    msg_id = b"1-0"
    messages = [
        (STREAM.encode(), [(msg_id, {b"event": event_json.encode()})])
    ]

    call_count = 0

    async def xreadgroup_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return messages
        raise asyncio.CancelledError

    mock_r = AsyncMock()
    mock_r.xgroup_create.return_value = True
    mock_r.xreadgroup.side_effect = xreadgroup_side_effect

    driver = _make_driver()

    with patch("shared.consumer.aioredis.from_url", return_value=mock_r), \
         patch("app.consumer.enricher") as mock_enricher:
        # Make the enricher call fail during post_batch
        mock_enricher.update_node_props_batch.side_effect = Exception("Neo4j timeout")
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(driver)

    # Message IS ACKed — handle() succeeded, post_batch() failure is separate
    mock_r.xack.assert_called_once_with(STREAM, GROUP, msg_id)


@pytest.mark.asyncio
async def test_consumer_handles_redis_loop_error():
    """Consumer survives a transient Redis error in the main loop."""
    call_count = 0

    async def xreadgroup_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("Redis temporarily unavailable")
        raise asyncio.CancelledError

    mock_r = AsyncMock()
    mock_r.xgroup_create.return_value = True
    mock_r.xreadgroup.side_effect = xreadgroup_side_effect

    driver = _make_driver()

    with patch("shared.consumer.aioredis.from_url", return_value=mock_r):
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(driver)

    # Consumer should have survived the first error and hit the second call
    assert mock_r.xreadgroup.call_count == 2


# New tests for GraphEnricherConsumer

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call as mock_call
from app.consumer import GraphEnricherConsumer


@pytest.mark.asyncio
async def test_consumer_batches_neo4j_writes():
    """post_batch flushes all accumulated records in one call each."""
    driver = _make_driver()
    consumer = GraphEnricherConsumer(driver)

    path1 = _make_execution_path()
    path2 = _make_execution_path(entrypoint_stable_id="sha256:fn_test_register")

    data1 = {"event": path1.model_dump_json().encode()}
    data2 = {"event": path2.model_dump_json().encode()}

    with patch("app.consumer.enricher") as mock_enricher:
        mock_enricher.update_node_props_batch = MagicMock()
        mock_enricher.upsert_dynamic_edges = MagicMock()
        mock_enricher.confirm_static_edges = MagicMock()
        mock_enricher.run_conflict_detector = MagicMock()
        mock_enricher.run_staleness_downgrade = MagicMock()

        # Accumulate two events
        await consumer.handle(data1)
        await consumer.handle(data2)
        assert len(consumer._batch_node_records) > 0, "should have accumulated node records"

        # post_batch must call each enricher function exactly once, with all accumulated data
        await consumer.post_batch()
        mock_enricher.update_node_props_batch.assert_called_once()
        mock_enricher.run_conflict_detector.assert_called_once()

    # Buffers cleared after post_batch
    assert consumer._batch_node_records == []
    assert consumer._batch_dynamic_edges == []
    assert consumer._batch_observed_ids == []
