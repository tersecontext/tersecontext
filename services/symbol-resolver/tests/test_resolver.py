# services/symbol-resolver/tests/test_resolver.py
from unittest.mock import MagicMock
import json
from datetime import datetime, timezone, timedelta
from app.resolver import parse_import_body, compute_path_hint, resolve_import, write_package_edge
from app.pending import push_pending, retry_pending
from app.models import PendingRef


# ── Test 1: from-import extracts symbol name ──────────────────────────────────

def test_extract_symbols_from_statement():
    result = parse_import_body("from auth.service import AuthService")
    assert result["kind"] == "from"
    assert result["names"] == ["AuthService"]
    assert result["module"] == "auth.service"
    assert result["dots"] == 0


# ── Test 2: multi-symbol from-import ─────────────────────────────────────────

def test_extract_symbols_multi():
    result = parse_import_body("from x import A, B")
    assert result["kind"] == "from"
    assert result["names"] == ["A", "B"]


# ── Test 3: aliased import uses alias.name, not alias.asname ─────────────────

def test_extract_symbols_aliased():
    result = parse_import_body("from x import AuthService as AS")
    assert result["kind"] == "from"
    assert result["names"] == ["AuthService"]   # .name, NOT "AS"
    assert "AS" not in result["names"]


# ── Test 12: relative import path hint computation ───────────────────────────

def test_relative_import_path_hint():
    # Single dot: strip filename from file_path
    hint = compute_path_hint(module="models", file_path="auth/service.py", dots=1)
    assert hint == "auth/"

def test_relative_import_path_hint_double_dot():
    # Two dots: strip filename + one directory
    hint = compute_path_hint(module="models", file_path="auth/views/handler.py", dots=2)
    assert hint == "auth/"

def test_plain_import_path_hint():
    # Non-relative: convert dots in module name to slashes
    hint = compute_path_hint(module="auth.service", file_path="views.py", dots=0)
    assert hint == "auth/service"


# ── Helper: mock driver ────────────────────────────────────────────────────────

def _make_driver(*query_results):
    """
    Build a mock Neo4j driver whose session.run().data() returns each item
    in query_results in sequence (one per run() call).
    """
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.return_value.data.side_effect = list(query_results)
    return driver, session


# ── Test 4: plain 'import bcrypt' → Package MERGE, no lookup ────────────────

def test_external_package_import():
    driver, session = _make_driver(
        [{"c": 1}],   # write_package_edge returns a row (importer exists)
    )
    result = write_package_edge(driver, "importer-sid", "bcrypt", "myrepo")
    assert result is True
    session.run.assert_called_once()
    query = session.run.call_args[0][0]
    assert "MERGE (pkg:Package" in query
    assert "IMPORTS" in query


# ── Test 5: exact qualified_name match → IMPORTS edge ────────────────────────

def test_exact_match_writes_imports_edge():
    driver, session = _make_driver(
        [{"stable_id": "target-sid"}],   # exact lookup hits
        [{"c": 1}],                       # edge write succeeds
    )
    result = resolve_import(driver, "importer-sid", "AuthService", "auth/service", "myrepo")
    assert result is True
    assert session.run.call_count == 2


# ── Test 6: exact miss, path hint hits → IMPORTS edge ────────────────────────

def test_path_hint_fallback():
    driver, session = _make_driver(
        [],                               # exact lookup misses
        [{"stable_id": "target-sid"}],   # path-hint lookup hits
        [{"c": 1}],                       # edge write succeeds
    )
    result = resolve_import(driver, "importer-sid", "AuthService", "auth/service", "myrepo")
    assert result is True
    assert session.run.call_count == 3


# ── Test 7: both lookups fail → return False ─────────────────────────────────

def test_both_lookups_fail_pushes_pending():
    driver, session = _make_driver(
        [],   # exact lookup misses
        [],   # path-hint lookup misses
    )
    result = resolve_import(driver, "importer-sid", "AuthService", "auth/service", "myrepo")
    assert result is False
    assert session.run.call_count == 2


# ── Test 8: importer not yet in Neo4j → return False ─────────────────────────

def test_importer_not_yet_in_neo4j_pushes_pending():
    driver, session = _make_driver(
        [{"stable_id": "target-sid"}],   # target found
        [],                               # edge write returns no rows (importer missing)
    )
    result = resolve_import(driver, "missing-importer-sid", "AuthService", "auth/service", "myrepo")
    assert result is False


def _make_ref(**kwargs) -> PendingRef:
    defaults = dict(
        source_stable_id="importer-sid",
        target_name="AuthService",
        path_hint="auth/service",
        edge_type="IMPORTS",
        repo="myrepo",
        attempted_at=datetime.now(timezone.utc),
        attempt_count=0,
    )
    defaults.update(kwargs)
    return PendingRef(**defaults)


def _ref_json(ref: PendingRef) -> bytes:
    return ref.model_dump_json().encode()


# ── Test 9: retry resolves a pending ref ──────────────────────────────────────

def test_pending_retry_resolves():
    ref = _make_ref()
    r = MagicMock()
    r.pipeline.return_value.execute.return_value = ([_ref_json(ref)], 1)

    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    # resolve_import will succeed: exact lookup finds target, edge write succeeds
    session.run.return_value.data.side_effect = [
        [{"stable_id": "target-sid"}],  # exact lookup
        [{"c": 1}],                      # edge write
    ]

    retry_pending(driver, r, "myrepo")

    r.pipeline.assert_called_once()
    # Ref was resolved — should NOT be pushed back
    r.rpush.assert_not_called()


# ── Test 10: retry resolves because importer now exists ───────────────────────

def test_pending_retry_resolves_on_importer_arrival():
    ref = _make_ref(attempt_count=1)
    r = MagicMock()
    r.pipeline.return_value.execute.return_value = ([_ref_json(ref)], 1)

    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.return_value.data.side_effect = [
        [{"stable_id": "target-sid"}],  # exact lookup finds target
        [{"c": 1}],                      # edge write now succeeds (importer arrived)
    ]

    retry_pending(driver, r, "myrepo")

    r.pipeline.assert_called_once()
    r.rpush.assert_not_called()


# ── Test 11a: expiry by attempt_count ─────────────────────────────────────────

def test_pending_expiry_drops_old_ref_by_count():
    ref = _make_ref(attempt_count=6)  # > 5 → drop
    r = MagicMock()
    r.pipeline.return_value.execute.return_value = ([_ref_json(ref)], 1)

    driver = MagicMock()
    retry_pending(driver, r, "myrepo")

    r.pipeline.assert_called_once()
    r.rpush.assert_not_called()
    driver.session.assert_not_called()  # no Neo4j call attempted


# ── Test 11b: expiry by age ───────────────────────────────────────────────────

def test_pending_expiry_drops_old_ref_by_age():
    old_time = datetime.now(timezone.utc) - timedelta(hours=25)
    ref = _make_ref(attempted_at=old_time, attempt_count=1)  # old but few attempts
    r = MagicMock()
    r.pipeline.return_value.execute.return_value = ([_ref_json(ref)], 1)

    driver = MagicMock()
    retry_pending(driver, r, "myrepo")

    r.rpush.assert_not_called()
