# services/symbol-resolver/tests/test_resolver.py
from unittest.mock import MagicMock
from app.resolver import parse_import_body, compute_path_hint, resolve_import, write_package_edge


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
