import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_consumer_polls_readiness_key_before_indexing():
    """Consumer waits for graph-writer:repo-ready key before calling index_repo."""
    called_with = []

    async def fake_index(repo, path, servers, driver):
        called_with.append(repo)

    mock_redis = AsyncMock()
    # First call: key absent; second call: key present.
    mock_redis.get.side_effect = [None, b"1"]

    with patch("app.consumer.index_repo", fake_index):
        from app.consumer import process_message
        await process_message(
            mock_redis, b"my-repo", b"/repos/my-repo", b"abc123",
            language_servers={}, driver=MagicMock(),
            poll_interval=0, poll_timeout=1,
        )

    assert called_with == ["my-repo"]
    # Redis.get was called for the readiness key.
    mock_redis.get.assert_called_with("graph-writer:repo-ready:my-repo:abc123")


@pytest.mark.asyncio
async def test_consumer_proceeds_after_timeout():
    """Consumer proceeds with best-effort indexing after poll_timeout."""
    called_with = []

    async def fake_index(repo, path, servers, driver):
        called_with.append(repo)

    mock_redis = AsyncMock()
    mock_redis.get.return_value = None  # always absent — should timeout

    with patch("app.consumer.index_repo", fake_index):
        from app.consumer import process_message
        await process_message(
            mock_redis, b"my-repo", b"/repos/my-repo", b"abc123",
            language_servers={}, driver=MagicMock(),
            poll_interval=0,  # break after first poll — no sleep, no timing sensitivity
            poll_timeout=1,   # generous timeout so the loop enters and polls once
        )

    assert called_with == ["my-repo"]
    # r.get should have been called at least once before timeout
    mock_redis.get.assert_called_with("graph-writer:repo-ready:my-repo:abc123")
