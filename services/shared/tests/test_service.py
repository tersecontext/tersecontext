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


def test_ready_required_dep_failing_returns_503_unavailable():
    svc = ServiceBase("my-svc", "1.0.0")

    async def check_redis() -> str | None:
        return "redis: connection refused"

    svc.add_dep_checker(check_redis)
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert "check_redis" in body["deps"]
    assert body["deps"]["check_redis"]["status"] == "error"
    assert body["deps"]["check_redis"]["required"] is True
    assert "connection refused" in body["deps"]["check_redis"]["error"]


def test_ready_optional_dep_failing_returns_200_degraded():
    svc = ServiceBase("my-svc", "1.0.0")

    async def check_neo4j() -> str | None:
        return "neo4j: unreachable"

    svc.add_dep_checker(check_neo4j, name="neo4j", required=False)
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["deps"]["neo4j"]["required"] is False
    assert body["deps"]["neo4j"]["status"] == "error"


def test_ready_custom_name_overrides_function_name():
    svc = ServiceBase("my-svc", "1.0.0")

    async def check_something() -> str | None:
        return None

    svc.add_dep_checker(check_something, name="mydb")
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    body = resp.json()
    assert "mydb" in body["deps"]
    assert "check_something" not in body["deps"]


def test_ready_passing_dep_appears_in_deps_ok():
    svc = ServiceBase("my-svc", "1.0.0")

    async def check_redis() -> str | None:
        return None

    svc.add_dep_checker(check_redis)
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    body = resp.json()
    assert body["status"] == "ok"
    assert body["deps"]["check_redis"]["status"] == "ok"
    assert body["deps"]["check_redis"]["required"] is True
    assert "error" not in body["deps"]["check_redis"]


def test_ready_no_checkers_returns_ok_empty_deps():
    svc = ServiceBase("my-svc", "1.0.0")
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["deps"] == {}


def test_ready_required_and_optional_both_failing_returns_503():
    svc = ServiceBase("my-svc", "1.0.0")

    async def check_required() -> str | None:
        return "db: down"

    async def check_optional() -> str | None:
        return "cache: down"

    svc.add_dep_checker(check_required, name="db", required=True)
    svc.add_dep_checker(check_optional, name="cache", required=False)
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "unavailable"


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


def test_validate_env_passes_when_all_vars_set(monkeypatch):
    monkeypatch.setenv("FOO", "bar")
    monkeypatch.setenv("BAZ", "qux")
    from shared.service import validate_env
    validate_env(["FOO", "BAZ"], "test-svc")  # must not raise or exit


def test_validate_env_exits_when_var_missing(monkeypatch, capsys):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    from shared.service import validate_env
    with pytest.raises(SystemExit) as exc_info:
        validate_env(["MISSING_VAR"], "test-svc")
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "MISSING_VAR" in captured.err
    assert "test-svc" in captured.err
    assert "usage.md" in captured.err


def test_validate_env_reports_all_missing_at_once(monkeypatch, capsys):
    monkeypatch.delenv("VAR_A", raising=False)
    monkeypatch.delenv("VAR_B", raising=False)
    from shared.service import validate_env
    with pytest.raises(SystemExit) as exc_info:
        validate_env(["VAR_A", "VAR_B"], "test-svc")
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "VAR_A" in captured.err
    assert "VAR_B" in captured.err


def test_validate_env_treats_empty_string_as_missing(monkeypatch, capsys):
    monkeypatch.setenv("EMPTY_VAR", "")
    from shared.service import validate_env
    with pytest.raises(SystemExit) as exc_info:
        validate_env(["EMPTY_VAR"], "test-svc")
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "EMPTY_VAR" in captured.err
    assert "test-svc" in captured.err
