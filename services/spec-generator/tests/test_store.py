import uuid
from unittest.mock import AsyncMock, MagicMock
import pytest

from app.models import CallSequenceItem, ExecutionPath


def _make_path(call_sequence=None, side_effects=None, repo="acme"):
    items = call_sequence or [
        CallSequenceItem(
            stable_id="sha256:fn_login",
            name="login",
            qualified_name="auth.login",
            hop=0,
            frequency_ratio=1.0,
            avg_ms=10.0,
        )
    ]
    return ExecutionPath(
        entrypoint_stable_id="sha256:fn_login",
        commit_sha="abc123",
        repo=repo,
        call_sequence=items,
        side_effects=side_effects or [],
        dynamic_only_edges=[],
        never_observed_static_edges=[],
        timing_p50_ms=10.0,
        timing_p99_ms=40.0,
    )


# ── upsert_spec ───────────────────────────────────────────────────────────────

async def test_upsert_spec_executes_insert_on_conflict():
    from app.store import SpecStore
    mock_conn = AsyncMock()
    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=mock_pool)
    mock_pool.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.__aexit__ = AsyncMock(return_value=False)

    store = SpecStore.__new__(SpecStore)
    store._pool = mock_pool

    path = _make_path()
    await store.upsert_spec(path, "some spec text")

    mock_conn.execute.assert_called_once()
    sql, *args = mock_conn.execute.call_args[0]
    assert "ON CONFLICT" in sql
    assert "behavior_specs" in sql
    assert "version" in sql
    # Verify positional args: node_stable_id, repo, commit_sha, spec_text, branch_coverage, observed_calls
    assert args[0] == "sha256:fn_login"
    assert args[1] == "acme"
    assert args[2] == "abc123"
    assert args[3] == "some spec text"
    assert args[4] == 1.0  # branch_coverage: 1 item with freq 1.0 → 100%
    assert args[5] == 1    # observed_calls: 1 item in call_sequence


async def test_upsert_spec_branch_coverage_all_observed():
    """All items with frequency_ratio > 0.0 → branch_coverage = 1.0"""
    from app.store import _compute_branch_coverage
    items = [
        CallSequenceItem(stable_id="a", name="a", qualified_name="m.a", hop=0, frequency_ratio=1.0, avg_ms=1.0),
        CallSequenceItem(stable_id="b", name="b", qualified_name="m.b", hop=1, frequency_ratio=0.5, avg_ms=1.0),
    ]
    assert _compute_branch_coverage(items) == 1.0


async def test_upsert_spec_branch_coverage_none_observed():
    """All items with frequency_ratio == 0.0 → branch_coverage = 0.0"""
    from app.store import _compute_branch_coverage
    items = [
        CallSequenceItem(stable_id="a", name="a", qualified_name="m.a", hop=0, frequency_ratio=0.0, avg_ms=1.0),
        CallSequenceItem(stable_id="b", name="b", qualified_name="m.b", hop=1, frequency_ratio=0.0, avg_ms=1.0),
    ]
    assert _compute_branch_coverage(items) == 0.0


async def test_upsert_spec_branch_coverage_mixed():
    """2 of 4 items observed → branch_coverage = 0.5"""
    from app.store import _compute_branch_coverage
    items = [
        CallSequenceItem(stable_id="a", name="a", qualified_name="m.a", hop=0, frequency_ratio=1.0, avg_ms=1.0),
        CallSequenceItem(stable_id="b", name="b", qualified_name="m.b", hop=1, frequency_ratio=0.5, avg_ms=1.0),
        CallSequenceItem(stable_id="c", name="c", qualified_name="m.c", hop=1, frequency_ratio=0.0, avg_ms=1.0),
        CallSequenceItem(stable_id="d", name="d", qualified_name="m.d", hop=2, frequency_ratio=0.0, avg_ms=1.0),
    ]
    assert _compute_branch_coverage(items) == 0.5


async def test_upsert_spec_branch_coverage_empty_call_sequence():
    from app.store import _compute_branch_coverage
    assert _compute_branch_coverage([]) is None


# ── upsert_qdrant ─────────────────────────────────────────────────────────────

async def test_upsert_qdrant_calls_embed_and_upsert():
    from app.store import SpecStore

    mock_provider = MagicMock()
    mock_provider.embed = AsyncMock(return_value=[[0.1] * 768])

    mock_qdrant = MagicMock()
    mock_qdrant.get_collections.return_value = MagicMock(collections=[])
    mock_qdrant.upsert = MagicMock()

    store = SpecStore.__new__(SpecStore)
    store._provider = mock_provider
    store._embedding_dim = 768
    store._qdrant = mock_qdrant

    path = _make_path()
    await store.upsert_qdrant(path, "login", "spec text here")

    mock_provider.embed.assert_called_once_with(["spec text here"])
    mock_qdrant.upsert.assert_called_once()
    _coll = mock_qdrant.upsert.call_args[1]["collection_name"]
    points_arg = mock_qdrant.upsert.call_args[1]["points"]
    assert _coll == "specs"
    assert len(points_arg) == 1
    payload = points_arg[0].payload
    assert payload["node_stable_id"] == "sha256:fn_login"
    assert payload["entrypoint_name"] == "login"
    assert payload["repo"] == "acme"
    assert payload["commit_sha"] == "abc123"


async def test_upsert_qdrant_point_id_is_stable():
    """Same path → same point id across calls."""
    from app.store import SpecStore

    mock_provider = MagicMock()
    mock_provider.embed = AsyncMock(return_value=[[0.1] * 768])
    mock_qdrant = MagicMock()
    mock_qdrant.upsert = MagicMock()

    store = SpecStore.__new__(SpecStore)
    store._provider = mock_provider
    store._embedding_dim = 768
    store._qdrant = mock_qdrant

    path = _make_path()
    await store.upsert_qdrant(path, "login", "text")
    await store.upsert_qdrant(path, "login", "text")

    ids = [call_args[1]["points"][0].id for call_args in mock_qdrant.upsert.call_args_list]
    assert ids[0] == ids[1]


# ── ensure_collection ─────────────────────────────────────────────────────────

async def test_ensure_collection_creates_if_absent():
    from app.store import SpecStore
    mock_qdrant = MagicMock()
    mock_qdrant.get_collections.return_value = MagicMock(collections=[])
    mock_qdrant.create_collection = MagicMock()

    store = SpecStore.__new__(SpecStore)
    store._qdrant = mock_qdrant
    store._embedding_dim = 768

    await store.ensure_collection()
    mock_qdrant.create_collection.assert_called_once()


async def test_ensure_collection_skips_if_exists():
    from app.store import SpecStore
    existing = MagicMock()
    existing.name = "specs"
    mock_qdrant = MagicMock()
    mock_qdrant.get_collections.return_value = MagicMock(collections=[existing])

    store = SpecStore.__new__(SpecStore)
    store._qdrant = mock_qdrant
    store._embedding_dim = 768

    await store.ensure_collection()
    mock_qdrant.create_collection.assert_not_called()


async def test_ensure_collection_suppresses_409():
    from app.store import SpecStore
    mock_qdrant = MagicMock()
    mock_qdrant.get_collections.return_value = MagicMock(collections=[])
    exc = Exception("conflict")
    exc.status_code = 409
    mock_qdrant.create_collection = MagicMock(side_effect=exc)

    store = SpecStore.__new__(SpecStore)
    store._qdrant = mock_qdrant
    store._embedding_dim = 768

    await store.ensure_collection()  # must not raise
