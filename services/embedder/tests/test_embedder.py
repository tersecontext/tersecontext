import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Models ────────────────────────────────────────────────────────────────────

def test_parsed_node_docstring_defaults_to_empty_string():
    from app.models import ParsedNode
    node = ParsedNode(
        stable_id="sha256:abc",
        node_hash="sha256:def",
        type="function",
        name="foo",
        signature="foo(x: int) -> str",
        docstring="",
        body="def foo(x: int) -> str: return str(x)",
        line_start=1,
        line_end=1,
    )
    assert node.docstring == ""
    assert node.parent_id is None


def test_parsed_file_event_ignores_extra_fields():
    """Parser emits deleted_nodes which is not in the formal contract — must be ignored."""
    from app.models import ParsedFileEvent
    evt = ParsedFileEvent.model_validate({
        "repo": "acme",
        "commit_sha": "abc123",
        "file_path": "src/foo.py",
        "language": "python",
        "nodes": [],
        "intra_file_edges": [],
        "deleted_nodes": ["sha256:x"],  # extra field not in contract
    })
    assert evt.repo == "acme"
    assert not hasattr(evt, "deleted_nodes")


def test_embedded_nodes_event_shape():
    from app.models import EmbeddedNode, EmbeddedNodesEvent
    node = EmbeddedNode(
        stable_id="sha256:abc",
        vector=[0.1, 0.2, 0.3],
        embed_text="foo foo(x: int) -> str",
        node_hash="sha256:def",
    )
    evt = EmbeddedNodesEvent(repo="acme", commit_sha="abc123", nodes=[node])
    assert evt.nodes[0].stable_id == "sha256:abc"
    assert len(evt.nodes[0].vector) == 3


# ── Providers ─────────────────────────────────────────────────────────────────

def test_ollama_provider_is_embedding_provider():
    from app.providers.base import EmbeddingProvider
    from app.providers.ollama import OllamaProvider
    provider = OllamaProvider()
    assert isinstance(provider, EmbeddingProvider)
    assert hasattr(provider, "embed")


def test_voyage_provider_is_embedding_provider():
    import os
    os.environ["VOYAGE_API_KEY"] = "test-key"
    try:
        from app.providers.base import EmbeddingProvider
        from app.providers.voyage import VoyageProvider
        provider = VoyageProvider()
        assert isinstance(provider, EmbeddingProvider)
        assert hasattr(provider, "embed")
    finally:
        os.environ.pop("VOYAGE_API_KEY", None)


def test_voyage_provider_raises_without_api_key():
    import os
    os.environ.pop("VOYAGE_API_KEY", None)
    from app.providers.voyage import VoyageProvider
    # VoyageProvider reads VOYAGE_API_KEY in __init__, not at module load —
    # no reload needed; instantiating with the key absent is sufficient.
    with pytest.raises(ValueError, match="VOYAGE_API_KEY"):
        VoyageProvider()
