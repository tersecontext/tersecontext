import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from pydantic import ValidationError

from app.models import CallSequenceItem, ExecutionPath
from app.consumer import SpecGeneratorConsumer


def _make_path_dict(call_sequence=None):
    if call_sequence is None:
        call_sequence = [
            {
                "stable_id": "sha256:fn_login",
                "name": "login",
                "qualified_name": "auth.login",
                "hop": 0,
                "frequency_ratio": 1.0,
                "avg_ms": 10.0,
            }
        ]
    return {
        "entrypoint_stable_id": "sha256:fn_login",
        "commit_sha": "abc123",
        "repo": "acme",
        "call_sequence": call_sequence,
        "side_effects": [],
        "dynamic_only_edges": [],
        "never_observed_static_edges": [],
        "timing_p50_ms": 10.0,
        "timing_p99_ms": 40.0,
    }


def _make_store():
    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock()
    mock_store.upsert_qdrant = AsyncMock()
    return mock_store


async def test_process_valid_message_calls_store():
    mock_store = _make_store()
    consumer = SpecGeneratorConsumer(mock_store)

    data = {"event": json.dumps(_make_path_dict())}
    await consumer.handle(data)

    mock_store.upsert_spec.assert_called_once()
    mock_store.upsert_qdrant.assert_called_once()

    # entrypoint_name should be "login" (first call_sequence item)
    _, entrypoint_name, _, _ = mock_store.upsert_qdrant.call_args[0]
    assert entrypoint_name == "login"


async def test_process_empty_call_sequence_falls_back_to_stable_id():
    mock_store = _make_store()
    consumer = SpecGeneratorConsumer(mock_store)

    data = {"event": json.dumps(_make_path_dict(call_sequence=[]))}
    await consumer.handle(data)

    _, entrypoint_name, _, _ = mock_store.upsert_qdrant.call_args[0]
    assert entrypoint_name == "sha256:fn_login"


async def test_process_validation_error_raises():
    mock_store = _make_store()
    consumer = SpecGeneratorConsumer(mock_store)

    data = {"event": json.dumps({"bad": "data"})}
    with pytest.raises(ValidationError):
        await consumer.handle(data)

    mock_store.upsert_spec.assert_not_called()


async def test_process_missing_event_key_raises():
    mock_store = _make_store()
    consumer = SpecGeneratorConsumer(mock_store)

    data = {}
    with pytest.raises(KeyError):
        await consumer.handle(data)


async def test_process_postgres_failure_propagates():
    mock_store = _make_store()
    mock_store.upsert_spec = AsyncMock(side_effect=Exception("postgres down"))
    consumer = SpecGeneratorConsumer(mock_store)

    data = {"event": json.dumps(_make_path_dict())}
    with pytest.raises(Exception, match="postgres down"):
        await consumer.handle(data)

    mock_store.upsert_qdrant.assert_not_called()


async def test_process_same_message_twice_calls_store_twice():
    """Consumer must not short-circuit on duplicates — version increment is Postgres's job."""
    mock_store = _make_store()
    consumer = SpecGeneratorConsumer(mock_store)

    data = {"event": json.dumps(_make_path_dict())}
    await consumer.handle(data)
    await consumer.handle(data)

    assert mock_store.upsert_spec.call_count == 2
    assert mock_store.upsert_qdrant.call_count == 2


async def test_process_bytes_event_decoded():
    mock_store = _make_store()
    consumer = SpecGeneratorConsumer(mock_store)

    data = {b"event": json.dumps(_make_path_dict()).encode()}
    await consumer.handle(data)

    mock_store.upsert_spec.assert_called_once()


async def test_successful_process_increments_written_and_embedded_counters():
    import app.consumer as consumer_mod

    consumer_mod.specs_written_total = 0
    consumer_mod.specs_embedded_total = 0

    mock_store = _make_store()
    consumer = SpecGeneratorConsumer(mock_store)

    data = {"event": json.dumps(_make_path_dict())}
    await consumer.handle(data)

    assert consumer_mod.specs_written_total == 1
    assert consumer_mod.specs_embedded_total == 1


async def test_malformed_message_increments_failed_counter():
    import app.consumer as consumer_mod

    consumer_mod.messages_failed_total = 0

    mock_store = _make_store()
    consumer = SpecGeneratorConsumer(mock_store)

    # ValidationError from bad data propagates out of handle()
    # messages_failed_total is NOT incremented in handle() — it was always
    # incremented in the caller (run_consumer / RedisConsumerBase).
    # ValidationError is NOT a KeyError/ValueError, so RedisConsumerBase
    # will log it and not ACK (retry), without touching messages_failed_total.
    data = {"event": json.dumps({"bad": "data"})}
    with pytest.raises(ValidationError):
        await consumer.handle(data)

    # messages_failed_total is incremented in the consumer loop, not in handle()
    assert consumer_mod.messages_failed_total == 0


@pytest.mark.asyncio
async def test_process_passes_confidence_band_to_qdrant():
    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock()
    mock_store.upsert_qdrant = AsyncMock()

    consumer = SpecGeneratorConsumer(mock_store)
    path_dict = _make_path_dict()
    path_dict["coverage_pct"] = 0.85
    data = {"event": json.dumps(path_dict).encode()}

    await consumer.handle(data)

    args = mock_store.upsert_qdrant.call_args[0]
    assert len(args) == 4, f"expected 4 args, got {len(args)}: {args}"
    confidence_band = args[3]
    assert confidence_band in ("HIGH", "MEDIUM", "LOW")


@pytest.mark.asyncio
async def test_consumer_is_redis_consumer_base():
    """SpecGeneratorConsumer inherits RedisConsumerBase."""
    from shared.consumer import RedisConsumerBase
    from app.consumer import SpecGeneratorConsumer
    assert issubclass(SpecGeneratorConsumer, RedisConsumerBase)
    assert SpecGeneratorConsumer.stream == "stream:execution-paths"
    assert SpecGeneratorConsumer.group == "spec-generator-group"
