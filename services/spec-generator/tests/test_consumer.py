import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from pydantic import ValidationError

from app.models import CallSequenceItem, ExecutionPath


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


async def test_process_valid_message_calls_store():
    from app.consumer import _process

    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock()
    mock_store.upsert_qdrant = AsyncMock()

    data = {"event": json.dumps(_make_path_dict())}
    await _process(data, mock_store)

    mock_store.upsert_spec.assert_called_once()
    mock_store.upsert_qdrant.assert_called_once()

    # entrypoint_name should be "login" (first call_sequence item)
    _, entrypoint_name, _ = mock_store.upsert_qdrant.call_args[0]
    assert entrypoint_name == "login"


async def test_process_empty_call_sequence_falls_back_to_stable_id():
    from app.consumer import _process

    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock()
    mock_store.upsert_qdrant = AsyncMock()

    data = {"event": json.dumps(_make_path_dict(call_sequence=[]))}
    await _process(data, mock_store)

    _, entrypoint_name, _ = mock_store.upsert_qdrant.call_args[0]
    assert entrypoint_name == "sha256:fn_login"


async def test_process_validation_error_raises():
    from app.consumer import _process

    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock()
    mock_store.upsert_qdrant = AsyncMock()

    data = {"event": json.dumps({"bad": "data"})}
    with pytest.raises(ValidationError):
        await _process(data, mock_store)

    mock_store.upsert_spec.assert_not_called()


async def test_process_missing_event_key_raises():
    from app.consumer import _process

    mock_store = MagicMock()
    data = {}
    with pytest.raises(KeyError):
        await _process(data, mock_store)


async def test_process_postgres_failure_propagates():
    from app.consumer import _process

    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock(side_effect=Exception("postgres down"))
    mock_store.upsert_qdrant = AsyncMock()

    data = {"event": json.dumps(_make_path_dict())}
    with pytest.raises(Exception, match="postgres down"):
        await _process(data, mock_store)

    mock_store.upsert_qdrant.assert_not_called()


async def test_process_same_message_twice_calls_store_twice():
    """Consumer must not short-circuit on duplicates — version increment is Postgres's job."""
    from app.consumer import _process

    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock()
    mock_store.upsert_qdrant = AsyncMock()

    data = {"event": json.dumps(_make_path_dict())}
    await _process(data, mock_store)
    await _process(data, mock_store)

    assert mock_store.upsert_spec.call_count == 2
    assert mock_store.upsert_qdrant.call_count == 2


async def test_process_bytes_event_decoded():
    from app.consumer import _process

    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock()
    mock_store.upsert_qdrant = AsyncMock()

    data = {b"event": json.dumps(_make_path_dict()).encode()}
    await _process(data, mock_store)

    mock_store.upsert_spec.assert_called_once()
