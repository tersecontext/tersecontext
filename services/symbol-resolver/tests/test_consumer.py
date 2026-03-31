# services/symbol-resolver/tests/test_consumer.py
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
import redis.exceptions

from app.consumer import _process_event, run_consumer, STREAM, GROUP
from app.models import ParsedFileEvent, ParsedNode, PendingRef


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_event(**overrides) -> ParsedFileEvent:
    defaults = dict(
        repo="test-repo",
        commit_sha="abc123",
        file_path="auth/service.py",
        language="python",
        nodes=[],
    )
    defaults.update(overrides)
    return ParsedFileEvent(**defaults)


def _import_node(body: str, stable_id: str = "sha256:imp1", name: str = "imp") -> ParsedNode:
    return ParsedNode(stable_id=stable_id, type="import", name=name, body=body)


def _non_import_node() -> ParsedNode:
    return ParsedNode(stable_id="sha256:cls1", type="class", name="Foo", body="class Foo: pass")


def _make_driver(*query_results):
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.return_value.data.side_effect = list(query_results)
    return driver, session


# ── _process_event tests ─────────────────────────────────────────────────────


def test_process_event_plain_import_calls_write_package_edge():
    """Plain 'import os' dispatches to write_package_edge."""
    event = _make_event(nodes=[_import_node("import os")])
    driver, session = _make_driver([{"c": 1}])  # write_package_edge succeeds
    r_sync = MagicMock()
    r_sync.pipeline.return_value.execute.return_value = ([], 0)

    _process_event(driver, r_sync, event)

    session.run.assert_called_once()
    query = session.run.call_args[0][0]
    assert "Package" in query


def test_process_event_from_import_resolve_succeeds():
    """'from auth.service import AuthService' resolves and writes IMPORTS edge."""
    event = _make_event(nodes=[_import_node("from auth.service import AuthService")])
    driver, session = _make_driver(
        [{"stable_id": "target-sid"}],  # exact lookup
        [{"c": 1}],                      # edge write
    )
    r_sync = MagicMock()
    r_sync.pipeline.return_value.execute.return_value = ([], 0)

    _process_event(driver, r_sync, event)

    # Should not push pending since resolve succeeded
    r_sync.rpush.assert_not_called()


def test_process_event_from_import_unresolved_pushes_pending():
    """Unresolved from-import pushes a PendingRef to Redis."""
    event = _make_event(nodes=[_import_node("from auth.service import AuthService")])
    driver, session = _make_driver(
        [],  # exact lookup misses
        [],  # path hint lookup misses
    )
    r_sync = MagicMock()
    r_sync.pipeline.return_value.execute.return_value = ([], 0)

    _process_event(driver, r_sync, event)

    # Should push pending ref
    assert r_sync.rpush.call_count >= 1
    pushed_key = r_sync.rpush.call_args_list[0][0][0]
    assert pushed_key == "pending_refs:test-repo"


def test_process_event_skips_non_import_nodes():
    """Non-import nodes (classes, functions) are ignored."""
    event = _make_event(nodes=[_non_import_node()])
    driver = MagicMock()
    r_sync = MagicMock()
    r_sync.pipeline.return_value.execute.return_value = ([], 0)

    _process_event(driver, r_sync, event)

    driver.session.assert_not_called()


def test_process_event_calls_retry_pending():
    """After processing imports, retry_pending is called for the repo."""
    event = _make_event(nodes=[])
    driver = MagicMock()
    r_sync = MagicMock()
    r_sync.pipeline.return_value.execute.return_value = ([], 0)

    with patch("app.consumer.retry_pending") as mock_retry:
        _process_event(driver, r_sync, event)
        mock_retry.assert_called_once_with(driver, r_sync, "test-repo")


def test_process_event_multi_plain_imports():
    """'import os, sys' writes two package edges."""
    event = _make_event(nodes=[_import_node("import os, sys")])
    driver, session = _make_driver(
        [{"c": 1}],  # os package edge
        [{"c": 1}],  # sys package edge
    )
    r_sync = MagicMock()
    r_sync.pipeline.return_value.execute.return_value = ([], 0)

    _process_event(driver, r_sync, event)

    assert session.run.call_count == 2


def test_process_event_multi_from_import_symbols():
    """'from x import A, B' resolves each symbol independently."""
    event = _make_event(nodes=[_import_node("from x import A, B")])
    driver, session = _make_driver(
        [{"stable_id": "sid-A"}], [{"c": 1}],  # A resolves
        [],                                      # B exact miss
        [],                                      # B path hint miss
    )
    r_sync = MagicMock()
    r_sync.pipeline.return_value.execute.return_value = ([], 0)

    _process_event(driver, r_sync, event)

    # B should be pushed as pending
    pushed_calls = [c for c in r_sync.rpush.call_args_list if "pending_refs" in str(c)]
    assert len(pushed_calls) >= 1


# ── run_consumer async tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consumer_creates_group_on_startup():
    """Consumer calls xgroup_create on startup."""
    r = AsyncMock()
    r_sync = MagicMock()

    # Make xreadgroup raise CancelledError to stop the loop
    r.xreadgroup.side_effect = [asyncio.CancelledError()]

    with patch("app.consumer.aioredis") as mock_aioredis, \
         patch("app.consumer.redis_sync") as mock_redis_sync:
        mock_aioredis.from_url.return_value = r
        mock_redis_sync.from_url.return_value = r_sync

        driver = MagicMock()
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(driver)

    r.xgroup_create.assert_awaited_once_with(STREAM, GROUP, id="0", mkstream=True)


@pytest.mark.asyncio
async def test_consumer_ignores_busygroup_error():
    """If xgroup_create raises BUSYGROUP (group already exists), continue normally."""
    r = AsyncMock()
    r.xgroup_create.side_effect = redis.exceptions.ResponseError("BUSYGROUP Consumer Group name already exists")
    r.xreadgroup.side_effect = [asyncio.CancelledError()]

    r_sync = MagicMock()

    with patch("app.consumer.aioredis") as mock_aioredis, \
         patch("app.consumer.redis_sync") as mock_redis_sync:
        mock_aioredis.from_url.return_value = r
        mock_redis_sync.from_url.return_value = r_sync

        driver = MagicMock()
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(driver)

    # Should not raise — BUSYGROUP is swallowed


@pytest.mark.asyncio
async def test_consumer_acks_messages():
    """Consumer ACKs each processed message."""

    msg_id = b"1-0"
    event = _make_event(nodes=[]).model_dump_json()
    messages = [[(STREAM.encode(), [(msg_id, {b"event": event.encode()})])]]

    r = AsyncMock()
    r.xreadgroup.side_effect = messages + [asyncio.CancelledError()]

    r_sync = MagicMock()
    r_sync.pipeline.return_value.execute.return_value = ([], 0)

    with patch("app.consumer.aioredis") as mock_aioredis, \
         patch("app.consumer.redis_sync") as mock_redis_sync, \
         patch("app.consumer._process_event") as mock_process:
        mock_aioredis.from_url.return_value = r
        mock_redis_sync.from_url.return_value = r_sync

        driver = MagicMock()
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(driver)

    r.xack.assert_awaited_once_with(STREAM, GROUP, msg_id)


@pytest.mark.asyncio
async def test_consumer_handles_malformed_message():
    """Malformed JSON is skipped and ACKed."""

    msg_id = b"1-0"
    messages = [[(STREAM.encode(), [(msg_id, {b"event": b"not-valid-json"})])]]

    r = AsyncMock()
    r.xreadgroup.side_effect = messages + [asyncio.CancelledError()]

    r_sync = MagicMock()

    with patch("app.consumer.aioredis") as mock_aioredis, \
         patch("app.consumer.redis_sync") as mock_redis_sync:
        mock_aioredis.from_url.return_value = r
        mock_redis_sync.from_url.return_value = r_sync

        driver = MagicMock()
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(driver)

    # Malformed message should still be ACKed
    r.xack.assert_awaited_once_with(STREAM, GROUP, msg_id)


@pytest.mark.asyncio
async def test_consumer_handles_missing_event_key():
    """Message without 'event' key is skipped and ACKed."""

    msg_id = b"1-0"
    messages = [[(STREAM.encode(), [(msg_id, {b"other_key": b"value"})])]]

    r = AsyncMock()
    r.xreadgroup.side_effect = messages + [asyncio.CancelledError()]

    r_sync = MagicMock()

    with patch("app.consumer.aioredis") as mock_aioredis, \
         patch("app.consumer.redis_sync") as mock_redis_sync:
        mock_aioredis.from_url.return_value = r
        mock_redis_sync.from_url.return_value = r_sync

        driver = MagicMock()
        with pytest.raises(asyncio.CancelledError):
            await run_consumer(driver)

    r.xack.assert_awaited_once_with(STREAM, GROUP, msg_id)
