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
