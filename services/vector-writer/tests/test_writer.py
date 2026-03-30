import json
import uuid
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_node(stable_id="sha256:abc", name="authenticate", node_type="function",
               file_path="auth/service.py", language="python",
               node_hash="sha256:def", vector=None):
    from app.models import EmbeddedNode
    return EmbeddedNode(
        stable_id=stable_id,
        vector=vector or [0.1, 0.2, 0.3],
        name=name,
        type=node_type,
        file_path=file_path,
        language=language,
        node_hash=node_hash,
    )


def _make_event(nodes=None, repo="acme-api"):
    from app.models import EmbeddedNodesEvent
    return EmbeddedNodesEvent(
        repo=repo,
        commit_sha="abc123",
        nodes=nodes if nodes is not None else [_make_node()],
    )


def _make_writer(embedding_dim=768):
    mock_client = MagicMock()
    mock_client.get_collections.return_value = MagicMock(collections=[])
    with patch("app.writer.QdrantClient", return_value=mock_client):
        from app.writer import QdrantWriter
        writer = QdrantWriter(url="http://localhost:6333", embedding_dim=embedding_dim)
    return writer, mock_client


# ── Models ─────────────────────────────────────────────────────────────────────

def test_embedded_node_shape():
    node = _make_node()
    assert node.stable_id == "sha256:abc"
    assert node.name == "authenticate"
    assert node.type == "function"
    assert node.file_path == "auth/service.py"
    assert node.language == "python"
    assert node.node_hash == "sha256:def"


def test_file_changed_event_deleted_nodes_defaults_to_empty():
    from app.models import FileChangedEvent
    event = FileChangedEvent.model_validate({
        "repo": "acme",
        "diff_type": "modified",
        "path": "src/foo.py",
        "language": "python",
        "changed_nodes": [],
        "added_nodes": [],
        # deleted_nodes intentionally omitted
    })
    assert event.deleted_nodes == []


def test_file_changed_event_ignores_extra_fields():
    from app.models import FileChangedEvent
    event = FileChangedEvent.model_validate({
        "repo": "acme",
        "deleted_nodes": ["sha256:x"],
        "some_unknown_field": "ignored",
    })
    assert event.repo == "acme"
    assert event.deleted_nodes == ["sha256:x"]


def test_embedded_nodes_event_commit_sha_is_optional():
    from app.models import EmbeddedNodesEvent
    event = EmbeddedNodesEvent(repo="acme", nodes=[])
    assert event.commit_sha is None


# ── writer.py: upsert_points ───────────────────────────────────────────────────

async def test_upsert_calls_client_with_correct_collection():
    writer, mock_client = _make_writer()
    await writer.upsert_points(_make_event())
    mock_client.upsert.assert_called_once()
    kwargs = mock_client.upsert.call_args.kwargs
    assert kwargs.get("collection_name") == "nodes"


async def test_upsert_point_ids_are_deterministic_uuid5():
    writer, mock_client = _make_writer()
    node = _make_node(stable_id="sha256:abc")
    await writer.upsert_points(_make_event(nodes=[node]))
    points = mock_client.upsert.call_args.kwargs["points"]
    expected_id = str(uuid.uuid5(uuid.NAMESPACE_OID, "sha256:abc"))
    assert points[0].id == expected_id


async def test_upsert_payload_contains_all_required_fields():
    writer, mock_client = _make_writer()
    node = _make_node(
        stable_id="sha256:abc", name="authenticate", node_type="function",
        file_path="auth/service.py", language="python", node_hash="sha256:def",
    )
    await writer.upsert_points(_make_event(nodes=[node], repo="acme-api"))
    payload = mock_client.upsert.call_args.kwargs["points"][0].payload
    assert payload["stable_id"] == "sha256:abc"
    assert payload["name"] == "authenticate"
    assert payload["type"] == "function"
    assert payload["file_path"] == "auth/service.py"
    assert payload["language"] == "python"
    assert payload["repo"] == "acme-api"
    assert payload["node_hash"] == "sha256:def"


async def test_upsert_empty_nodes_does_not_call_client():
    writer, mock_client = _make_writer()
    await writer.upsert_points(_make_event(nodes=[]))
    mock_client.upsert.assert_not_called()


# ── writer.py: delete_points ───────────────────────────────────────────────────

async def test_delete_calls_client_with_correct_string_uuids():
    writer, mock_client = _make_writer()
    stable_ids = ["sha256:abc", "sha256:def"]
    await writer.delete_points(stable_ids)
    mock_client.delete.assert_called_once()
    selector = mock_client.delete.call_args.kwargs["points_selector"]
    expected = [str(uuid.uuid5(uuid.NAMESPACE_OID, sid)) for sid in stable_ids]
    assert sorted(selector.points) == sorted(expected)


async def test_delete_empty_list_does_not_call_client():
    writer, mock_client = _make_writer()
    await writer.delete_points([])
    mock_client.delete.assert_not_called()


# ── writer.py: ensure_collection ──────────────────────────────────────────────

async def test_ensure_collection_creates_when_absent():
    writer, mock_client = _make_writer()
    mock_client.get_collections.return_value = MagicMock(collections=[])
    await writer.ensure_collection()
    mock_client.create_collection.assert_called_once()
    kwargs = mock_client.create_collection.call_args.kwargs
    assert kwargs["collection_name"] == "nodes"


async def test_ensure_collection_skips_create_when_present():
    writer, mock_client = _make_writer()
    existing = MagicMock()
    existing.name = "nodes"
    mock_client.get_collections.return_value = MagicMock(collections=[existing])
    await writer.ensure_collection()
    mock_client.create_collection.assert_not_called()


async def test_ensure_collection_ignores_already_exists_error():
    writer, mock_client = _make_writer()
    mock_client.get_collections.return_value = MagicMock(collections=[])
    error = Exception("Collection already exists")
    error.status_code = 409
    mock_client.create_collection.side_effect = error
    await writer.ensure_collection()  # must not raise


# ── writer.py: get_collections ─────────────────────────────────────────────────

async def test_get_collections_delegates_to_client():
    writer, mock_client = _make_writer()
    result = await writer.get_collections()
    mock_client.get_collections.assert_called_once()
    assert result == mock_client.get_collections.return_value


# ── consumer.py: _process_embedded ────────────────────────────────────────────

async def test_process_embedded_calls_upsert_points():
    from app.consumer import _process_embedded
    mock_writer = AsyncMock()
    event = _make_event()
    await _process_embedded(mock_writer, {"event": event.model_dump_json()})
    mock_writer.upsert_points.assert_called_once()


async def test_process_embedded_raises_on_upsert_failure():
    """Consumer loop must NOT XACK when upsert raises — verified by checking exception propagates."""
    from app.consumer import _process_embedded
    mock_writer = AsyncMock()
    mock_writer.upsert_points.side_effect = Exception("qdrant down")
    with pytest.raises(Exception, match="qdrant down"):
        await _process_embedded(mock_writer, {"event": _make_event().model_dump_json()})


async def test_process_embedded_raises_on_bad_json():
    """Consumer loop must XACK on bad JSON — verified by checking exception propagates."""
    from app.consumer import _process_embedded
    from pydantic import ValidationError
    mock_writer = AsyncMock()
    with pytest.raises((ValidationError, ValueError)):
        await _process_embedded(mock_writer, {"event": "not valid json at all {{"})
    mock_writer.upsert_points.assert_not_called()


async def test_process_embedded_returns_normally_when_nodes_empty():
    """Empty nodes → returns without calling upsert_points → caller will XACK."""
    from app.consumer import _process_embedded
    mock_writer = AsyncMock()
    empty_event = _make_event(nodes=[])
    await _process_embedded(mock_writer, {"event": empty_event.model_dump_json()})
    mock_writer.upsert_points.assert_not_called()


# ── consumer.py: _process_delete ──────────────────────────────────────────────

async def test_process_delete_calls_delete_points():
    from app.consumer import _process_delete
    mock_writer = AsyncMock()
    event_json = json.dumps({"repo": "acme", "deleted_nodes": ["sha256:abc", "sha256:def"]})
    await _process_delete(mock_writer, {"event": event_json})
    mock_writer.delete_points.assert_called_once_with(["sha256:abc", "sha256:def"])


async def test_process_delete_skips_when_deleted_nodes_empty():
    """Empty deleted_nodes → returns without calling delete_points → caller will XACK."""
    from app.consumer import _process_delete
    mock_writer = AsyncMock()
    event_json = json.dumps({"repo": "acme", "deleted_nodes": []})
    await _process_delete(mock_writer, {"event": event_json})
    mock_writer.delete_points.assert_not_called()


async def test_process_delete_raises_on_delete_failure():
    """Consumer loop must NOT XACK when delete raises — verified by checking exception propagates."""
    from app.consumer import _process_delete
    mock_writer = AsyncMock()
    mock_writer.delete_points.side_effect = Exception("qdrant down")
    event_json = json.dumps({"repo": "acme", "deleted_nodes": ["sha256:abc"]})
    with pytest.raises(Exception, match="qdrant down"):
        await _process_delete(mock_writer, {"event": event_json})


async def test_process_delete_raises_on_bad_json():
    """Consumer loop must XACK on bad JSON — verified by checking exception propagates."""
    from app.consumer import _process_delete
    from pydantic import ValidationError
    mock_writer = AsyncMock()
    with pytest.raises((ValidationError, ValueError)):
        await _process_delete(mock_writer, {"event": "{{not json}}"})
    mock_writer.delete_points.assert_not_called()
