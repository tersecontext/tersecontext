# services/symbol-resolver/tests/test_resolver.py
from app.resolver import parse_import_body, compute_path_hint


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
