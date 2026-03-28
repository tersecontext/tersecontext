import pytest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


# ── Models ────────────────────────────────────────────────────────────────────

def test_parsed_node_requires_fields():
    from app.models import ParsedNode
    node = ParsedNode(
        stable_id="sha256:abc",
        node_hash="sha256:def",
        type="function",
        name="foo",
        signature="foo()",
        docstring="",
        body="def foo(): pass",
        line_start=1,
        line_end=1,
    )
    assert node.parent_id is None


def test_parsed_file_event_has_deleted_nodes():
    from app.models import ParsedFileEvent
    evt = ParsedFileEvent(
        file_path="a.py",
        language="python",
        repo="repo",
        commit_sha="abc",
        nodes=[],
        intra_file_edges=[],
        deleted_nodes=["sha256:x"],
    )
    assert evt.deleted_nodes == ["sha256:x"]


# ── Parser ────────────────────────────────────────────────────────────────────

def test_parse_python_returns_root_node():
    from app.parser import parse
    source = b"def foo(): pass\n"
    root = parse(source, "python")
    assert root.type == "module"


def test_parse_unsupported_language_raises():
    from app.parser import parse
    with pytest.raises(ValueError, match="Unsupported language"):
        parse(b"", "cobol")


def test_parse_syntax_error_raises():
    from app.parser import parse
    with pytest.raises(SyntaxError):
        parse(b"def (", "python")


# ── Extractor — nodes ─────────────────────────────────────────────────────────

def _sample_nodes():
    from app.parser import parse
    from app.extractor import extract
    source = (FIXTURES / "sample.py").read_bytes()
    root = parse(source, "python")
    nodes, edges = extract(root, source, "test-repo", "auth/service.py")
    return nodes, edges, source


def test_extracts_import_nodes():
    nodes, _, _ = _sample_nodes()
    import_nodes = [n for n in nodes if n.type == "import"]
    assert len(import_nodes) >= 2  # import hashlib, from typing import Optional


def test_import_body_is_raw_source_slice():
    nodes, _, source = _sample_nodes()
    import_node = next(n for n in nodes if n.name == "hashlib")
    assert import_node.body == "import hashlib"
    assert import_node.body != ""


def test_extracts_class_node():
    nodes, _, _ = _sample_nodes()
    classes = [n for n in nodes if n.type == "class"]
    assert len(classes) == 1
    assert classes[0].name == "AuthService"


def test_extracts_method_nodes_with_parent_id():
    nodes, _, _ = _sample_nodes()
    cls = next(n for n in nodes if n.type == "class")
    methods = [n for n in nodes if n.type == "method"]
    assert len(methods) == 2
    assert all(m.parent_id == cls.stable_id for m in methods)


def test_method_qualified_name_not_in_output():
    from app.models import ParsedNode
    assert "qualified_name" not in ParsedNode.model_fields


def test_extracts_top_level_function():
    nodes, _, _ = _sample_nodes()
    fns = [n for n in nodes if n.type == "function"]
    assert len(fns) == 1
    assert fns[0].name == "compute_checksum"


def test_function_signature_with_types():
    source = b"def process(data: bytes) -> str:\n    return data.decode()\n"
    from app.parser import parse
    from app.extractor import extract
    root = parse(source, "python")
    nodes, _ = extract(root, source, "repo", "f.py")
    fn = next(n for n in nodes if n.name == "process")
    assert "data: bytes" in fn.signature
    assert "-> str" in fn.signature


def test_no_docstring_is_empty_string():
    source = b"def no_doc():\n    return 42\n"
    from app.parser import parse
    from app.extractor import extract
    root = parse(source, "python")
    nodes, _ = extract(root, source, "repo", "f.py")
    fn = next(n for n in nodes if n.name == "no_doc")
    assert fn.docstring == ""
    assert fn.docstring is not None


def test_method_docstring_extracted():
    nodes, _, _ = _sample_nodes()
    auth = next(n for n in nodes if n.name == "authenticate")
    assert auth.docstring != ""


def test_stable_id_is_deterministic():
    from app.parser import parse
    from app.extractor import extract
    source = (FIXTURES / "sample.py").read_bytes()
    root1 = parse(source, "python")
    root2 = parse(source, "python")
    nodes1, _ = extract(root1, source, "repo", "auth/service.py")
    nodes2, _ = extract(root2, source, "repo", "auth/service.py")
    assert {n.stable_id for n in nodes1} == {n.stable_id for n in nodes2}


def test_node_hash_changes_when_body_changes():
    from app.parser import parse
    from app.extractor import extract
    s1 = b"def my_func():\n    return 1\n"
    s2 = b"def my_func():\n    return 2\n"
    n1, _ = extract(parse(s1, "python"), s1, "repo", "f.py")
    n2, _ = extract(parse(s2, "python"), s2, "repo", "f.py")
    fn1 = next(n for n in n1 if n.name == "my_func")
    fn2 = next(n for n in n2 if n.name == "my_func")
    assert fn1.node_hash != fn2.node_hash


def test_calls_edge_detected():
    nodes, edges, _ = _sample_nodes()
    auth = next(n for n in nodes if n.name == "authenticate")
    hash_pw = next(n for n in nodes if n.name == "_hash_password")
    call_edge = next(
        (e for e in edges if e.source_stable_id == auth.stable_id and e.target_stable_id == hash_pw.stable_id),
        None,
    )
    assert call_edge is not None, "Expected CALLS edge from authenticate to _hash_password"
    assert call_edge.type == "CALLS"
