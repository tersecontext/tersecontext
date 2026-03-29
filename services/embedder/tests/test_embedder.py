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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_node(name: str, signature: str, docstring: str = "", body: str = "pass", node_hash: str = "sha256:new") -> "ParsedNode":
    from app.models import ParsedNode
    return ParsedNode(
        stable_id=f"sha256:{name}",
        node_hash=node_hash,
        type="function",
        name=name,
        signature=signature,
        docstring=docstring,
        body=body,
        line_start=1,
        line_end=1,
    )


# ── build_embed_text ──────────────────────────────────────────────────────────

def test_build_embed_text_no_docstring():
    from app.embedder import build_embed_text
    node = _make_node("foo", "foo(x: int) -> str", docstring="")
    result = build_embed_text(node)
    assert result == "foo foo(x: int) -> str"


def test_build_embed_text_with_docstring():
    from app.embedder import build_embed_text
    node = _make_node("foo", "foo(x: int) -> str", docstring="Does something useful")
    result = build_embed_text(node)
    assert result == "foo foo(x: int) -> str Does something useful"


def test_build_embed_text_never_includes_body():
    from app.embedder import build_embed_text
    node = _make_node("foo", "foo()", body="x = 1\ny = 2\nreturn x + y")
    result = build_embed_text(node)
    assert "x = 1" not in result
    assert "return x + y" not in result


# ── embed_nodes ───────────────────────────────────────────────────────────────

async def test_embed_nodes_batching():
    """130 nodes should produce exactly 3 provider.embed() calls: 64+64+2."""
    from app.embedder import embed_nodes
    nodes = [_make_node(f"fn{i}", f"fn{i}()") for i in range(130)]
    neo4j_cache: dict = {}  # all new

    call_count = 0
    batch_sizes = []

    async def counting_embed(texts):
        nonlocal call_count
        call_count += 1
        batch_sizes.append(len(texts))
        return [[0.1] * 768 for _ in texts]

    mock_provider = MagicMock()
    mock_provider.embed = counting_embed
    await embed_nodes(nodes, neo4j_cache, mock_provider, batch_size=64)

    assert call_count == 3
    assert batch_sizes == [64, 64, 2]


async def test_embed_nodes_skips_unchanged():
    """Nodes whose node_hash matches the Neo4j cache are excluded from output."""
    from app.embedder import embed_nodes
    unchanged = _make_node("old_fn", "old_fn()", node_hash="sha256:old")
    new_node = _make_node("new_fn", "new_fn()", node_hash="sha256:new")
    neo4j_cache = {unchanged.stable_id: "sha256:old"}  # hash matches

    async def fake_embed(texts):
        return [[0.1] * 768 for _ in texts]

    mock_provider = MagicMock()
    mock_provider.embed = fake_embed

    result = await embed_nodes([unchanged, new_node], neo4j_cache, mock_provider)

    stable_ids = [n.stable_id for n in result]
    assert unchanged.stable_id not in stable_ids
    assert new_node.stable_id in stable_ids


async def test_embed_nodes_includes_new_nodes():
    """Nodes absent from Neo4j cache are always embedded."""
    from app.embedder import embed_nodes
    node = _make_node("brand_new", "brand_new()", node_hash="sha256:fresh")
    neo4j_cache: dict = {}  # node not known to Neo4j

    async def fake_embed(texts):
        return [[0.5] * 768 for _ in texts]

    mock_provider = MagicMock()
    mock_provider.embed = fake_embed

    result = await embed_nodes([node], neo4j_cache, mock_provider)
    assert len(result) == 1
    assert result[0].stable_id == node.stable_id


async def test_embed_nodes_all_unchanged_returns_empty():
    """When all nodes are unchanged, output is an empty list."""
    from app.embedder import embed_nodes
    node = _make_node("fn", "fn()", node_hash="sha256:same")
    neo4j_cache = {node.stable_id: "sha256:same"}

    mock_provider = MagicMock()
    mock_provider.embed = AsyncMock()  # should not be called

    result = await embed_nodes([node], neo4j_cache, mock_provider)

    assert result == []
    mock_provider.embed.assert_not_called()


async def test_embed_nodes_neo4j_unreachable_treats_all_as_new():
    """When Neo4j cache is empty (unreachable), all nodes are embedded."""
    from app.embedder import embed_nodes
    nodes = [_make_node(f"fn{i}", f"fn{i}()") for i in range(3)]
    neo4j_cache: dict = {}  # empty = all treated as new

    call_count = 0

    async def counting_embed(texts):
        nonlocal call_count
        call_count += 1
        return [[0.1] * 768 for _ in texts]

    mock_provider = MagicMock()
    mock_provider.embed = counting_embed

    result = await embed_nodes(nodes, neo4j_cache, mock_provider)
    assert len(result) == 3
