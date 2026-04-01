import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shared.service import ServiceBase
from shared.consumer import RedisConsumerBase


def _app(svc: ServiceBase) -> FastAPI:
    """Mount svc.router onto a throwaway FastAPI app for testing."""
    app = FastAPI()
    app.include_router(svc.router)
    return app


def test_health_returns_ok():
    svc = ServiceBase("my-svc", "1.0.0")
    client = TestClient(_app(svc))
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "my-svc", "version": "1.0.0"}


def test_ready_no_checkers_returns_ok():
    svc = ServiceBase("my-svc", "1.0.0")
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ready_failing_checker_returns_503():
    svc = ServiceBase("my-svc", "1.0.0")

    async def bad_dep() -> str | None:
        return "redis: connection refused"

    svc.add_dep_checker(bad_dep)
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    assert resp.status_code == 503
    assert "redis" in resp.json()["errors"][0]


def test_ready_passing_checker_returns_ok():
    svc = ServiceBase("my-svc", "1.0.0")

    async def good_dep() -> str | None:
        return None

    svc.add_dep_checker(good_dep)
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    assert resp.status_code == 200


class EchoConsumer(RedisConsumerBase):
    stream = "stream:test"
    group = "test-group"

    def __init__(self):
        self.handled: list[dict] = []

    async def handle(self, data: dict) -> None:
        self.handled.append(data)


@pytest.mark.asyncio
async def test_consumer_calls_handle_for_each_event():
    consumer = EchoConsumer()
    event_data = {"event": json.dumps({"foo": "bar"}).encode()}

    fake_messages = [("stream:test", [(b"1-0", event_data)])]

    mock_redis = AsyncMock()
    mock_redis.xreadgroup = AsyncMock(side_effect=[fake_messages, asyncio.CancelledError()])
    mock_redis.xack = AsyncMock()
    mock_redis.xgroup_create = AsyncMock(return_value=True)
    mock_redis.aclose = AsyncMock()

    with patch("shared.consumer.aioredis.from_url", return_value=mock_redis):
        with pytest.raises(asyncio.CancelledError):
            await consumer.run("redis://localhost:6379")

    assert len(consumer.handled) == 1
    assert consumer.handled[0] == event_data


@pytest.mark.asyncio
async def test_consumer_sends_to_dlq_on_bad_message():
    class BadConsumer(RedisConsumerBase):
        stream = "stream:test"
        group = "test-group"

        async def handle(self, data: dict) -> None:
            raise KeyError("missing 'event' key")

    consumer = BadConsumer()
    event_data = {b"bad": b"data"}
    fake_messages = [("stream:test", [(b"1-0", event_data)])]

    mock_redis = AsyncMock()
    mock_redis.xreadgroup = AsyncMock(side_effect=[fake_messages, asyncio.CancelledError()])
    mock_redis.xack = AsyncMock()
    mock_redis.xadd = AsyncMock()
    mock_redis.xgroup_create = AsyncMock(return_value=True)
    mock_redis.aclose = AsyncMock()

    with patch("shared.consumer.aioredis.from_url", return_value=mock_redis):
        with pytest.raises(asyncio.CancelledError):
            await consumer.run("redis://localhost:6379")

    mock_redis.xadd.assert_called_once()
    dlq_stream = mock_redis.xadd.call_args[0][0]
    assert "dlq" in dlq_stream


@pytest.mark.asyncio
async def test_consumer_calls_post_batch_after_batch():
    """post_batch() must be called after each batch of events."""
    post_batch_called = []

    class BatchConsumer(RedisConsumerBase):
        stream = "stream:test"
        group = "test-group"

        async def handle(self, data: dict) -> None:
            pass

        async def post_batch(self) -> None:
            post_batch_called.append(True)

    consumer = BatchConsumer()
    event_data = {b"event": b"{}"}
    fake_messages = [("stream:test", [(b"1-0", event_data)])]

    mock_redis = AsyncMock()
    mock_redis.xreadgroup = AsyncMock(side_effect=[fake_messages, asyncio.CancelledError()])
    mock_redis.xack = AsyncMock()
    mock_redis.xgroup_create = AsyncMock(return_value=True)
    mock_redis.aclose = AsyncMock()

    with patch("shared.consumer.aioredis.from_url", return_value=mock_redis):
        with pytest.raises(asyncio.CancelledError):
            await consumer.run("redis://localhost:6379")

    assert len(post_batch_called) == 1


@pytest.mark.asyncio
async def test_consumer_does_not_ack_on_transient_error():
    """Generic exceptions must not ACK — message should be redelivered."""
    class TransientConsumer(RedisConsumerBase):
        stream = "stream:test"
        group = "test-group"

        async def handle(self, data: dict) -> None:
            raise RuntimeError("transient failure")

    consumer = TransientConsumer()
    event_data = {b"event": b"{}"}
    fake_messages = [("stream:test", [(b"1-0", event_data)])]

    mock_redis = AsyncMock()
    mock_redis.xreadgroup = AsyncMock(side_effect=[fake_messages, asyncio.CancelledError()])
    mock_redis.xack = AsyncMock()
    mock_redis.xgroup_create = AsyncMock(return_value=True)
    mock_redis.aclose = AsyncMock()

    with patch("shared.consumer.aioredis.from_url", return_value=mock_redis):
        with pytest.raises(asyncio.CancelledError):
            await consumer.run("redis://localhost:6379")

    mock_redis.xack.assert_not_called()
