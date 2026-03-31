# Parser Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the parser microservice that consumes `FileChanged` events from Redis, parses Python source files with Tree-sitter, and emits `ParsedFile` events downstream.

**Architecture:** FastAPI service with a background Redis Stream consumer. Tree-sitter grammars are loaded once at startup via a language registry dict. Extraction, filtering, and consumer logic are in separate focused modules.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, redis-py, tree-sitter 0.23+, tree-sitter-python, Pydantic v2, pytest, httpx

---

## Spec

`docs/superpowers/specs/2026-03-27-parser-service-design.md`

**Key discrepancies vs. CLAUDE.md — spec takes precedence:**
- `qualified_name` is internal only — do NOT emit it in `ParsedNode`
- Import nodes have `body` = raw source slice (not empty string)
- `diff_type=deleted` DOES emit a `ParsedFileEvent` (not suppressed)
- Use `tree-sitter-python` (per-language package, tree-sitter 0.23+ API) — NOT `tree_sitter_languages` (the old bundled package). User explicitly chose `tree-sitter-python` for better Python 3.12 compatibility.

---

## File Map

| File | Responsibility |
|---|---|
| `services/parser/app/__init__.py` | Package marker |
| `services/parser/app/models.py` | Pydantic: FileChangedEvent, ParsedNode, IntraFileEdge, ParsedFileEvent |
| `services/parser/app/parser.py` | PARSERS registry, `parse()` function |
| `services/parser/app/extractor.py` | `extract()`, `apply_diff_filter()`, hash helpers |
| `services/parser/app/consumer.py` | `_consumer_loop()`, `_process()`, `run_consumer()` |
| `services/parser/app/main.py` | FastAPI app, `/health` `/ready` `/metrics`, lifespan |
| `services/parser/tests/__init__.py` | Package marker |
| `services/parser/tests/test_parser.py` | All unit tests |
| `services/parser/tests/fixtures/sample.py` | Fixture: class+methods, top-level fn, imports |
| `services/parser/pyproject.toml` | Dependencies + dev extras |
| `services/parser/requirements.txt` | Flat dep list for Docker |
| `services/parser/Dockerfile` | Production image |

---

## Task 1: Scaffold

**Files:**
- Create: `services/parser/app/__init__.py`
- Create: `services/parser/tests/__init__.py`
- Create: `services/parser/tests/fixtures/__init__.py`
- Create: `services/parser/pyproject.toml`
- Create: `services/parser/requirements.txt`

- [ ] **Step 1: Create package markers**

```bash
mkdir -p services/parser/app
mkdir -p services/parser/tests/fixtures
touch services/parser/app/__init__.py
touch services/parser/tests/__init__.py
touch services/parser/tests/fixtures/__init__.py
```

- [ ] **Step 2: Create `services/parser/pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "tersecontext-parser"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "redis>=5.0.0",
    "tree-sitter>=0.23.0",
    "tree-sitter-python>=0.23.0",
    "pydantic>=2.0.0",
    "prometheus-client>=0.20.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "httpx>=0.27.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: Create `services/parser/requirements.txt`**

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
redis>=5.0.0
tree-sitter>=0.23.0
tree-sitter-python>=0.23.0
pydantic>=2.0.0
prometheus-client>=0.20.0
```

- [ ] **Step 4: Install deps**

```bash
cd services/parser
pip install -e ".[dev]"
```

Expected: installs without errors.

- [ ] **Step 5: Commit scaffold**

```bash
git add services/parser/
git commit -m "feat(parser): scaffold directory structure and dependencies"
```

---

## Task 2: Pydantic Models

**Files:**
- Create: `services/parser/app/models.py`
- Test: `services/parser/tests/test_parser.py` (models section)

- [ ] **Step 1: Write failing model test**

Create `services/parser/tests/test_parser.py`:

```python
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
```

- [ ] **Step 2: Run test — expect failure**

```bash
cd services/parser
pytest tests/test_parser.py::test_parsed_node_requires_fields -v
```

Expected: `ModuleNotFoundError` or `ImportError`.

- [ ] **Step 3: Implement `services/parser/app/models.py`**

```python
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class FileChangedEvent(BaseModel):
    repo: str
    commit_sha: str
    path: str
    language: str
    diff_type: str  # added | modified | deleted | full_rescan
    changed_nodes: list[str]
    added_nodes: list[str]
    deleted_nodes: list[str]


class ParsedNode(BaseModel):
    stable_id: str
    node_hash: str
    type: str
    name: str
    signature: str
    docstring: str
    body: str
    line_start: int
    line_end: int
    parent_id: Optional[str] = None


class IntraFileEdge(BaseModel):
    source_stable_id: str
    target_stable_id: str
    type: str  # CALLS


class ParsedFileEvent(BaseModel):
    file_path: str
    language: str
    repo: str
    commit_sha: str
    nodes: list[ParsedNode]
    intra_file_edges: list[IntraFileEdge]
    deleted_nodes: list[str]
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_parser.py -k "test_parsed" -v
```

Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/parser/app/models.py services/parser/tests/test_parser.py
git commit -m "feat(parser): add Pydantic models"
```

---

## Task 3: Tree-sitter Parser Module

**Files:**
- Create: `services/parser/app/parser.py`
- Modify: `services/parser/tests/test_parser.py`

- [ ] **Step 1: Add failing parser test**

Append to `tests/test_parser.py`:

```python
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
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_parser.py -k "test_parse" -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `services/parser/app/parser.py`**

```python
import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Node

PARSERS: dict[str, Language] = {
    "python": Language(tspython.language()),
}


def parse(source_bytes: bytes, language: str) -> Node:
    """Parse source bytes for the given language. Returns the root AST node.

    Raises ValueError for unsupported languages.
    Grammar objects are loaded once at module import — never call this
    function with the intent to reload grammars per file.
    """
    if language not in PARSERS:
        raise ValueError(f"Unsupported language: {language!r}")
    parser = Parser(PARSERS[language])
    tree = parser.parse(source_bytes)
    return tree.root_node
```

- [ ] **Step 4: Run — expect pass**

```bash
pytest tests/test_parser.py -k "test_parse" -v
```

Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/parser/app/parser.py services/parser/tests/test_parser.py
git commit -m "feat(parser): add tree-sitter parser module with language registry"
```

---

## Task 4: Test Fixture + Node Extraction

**Files:**
- Create: `services/parser/tests/fixtures/sample.py`
- Create: `services/parser/app/extractor.py` (node extraction only)
- Modify: `services/parser/tests/test_parser.py`

- [ ] **Step 1: Create `tests/fixtures/sample.py`**

This file is the ground truth for tests. It must have: a class with 2 methods (one calling the other), a top-level function, and imports.

```python
import hashlib
from typing import Optional


class AuthService:
    """Handles user authentication."""

    def authenticate(self, username: str, password: str) -> Optional[str]:
        """Validates credentials and returns a token, or None."""
        hashed = self._hash_password(password)
        if hashed:
            return f"token:{username}"
        return None

    def _hash_password(self, password: str) -> str:
        return hashlib.sha256(password.encode()).hexdigest()


def compute_checksum(data: bytes) -> str:
    """Compute MD5 checksum of data."""
    return hashlib.md5(data).hexdigest()
```

- [ ] **Step 2: Add failing node extraction tests**

Append to `tests/test_parser.py`:

```python
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
    nodes, _, _ = _sample_nodes()
    for node in nodes:
        assert not hasattr(node, "qualified_name") or "qualified_name" not in node.model_fields


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
```

- [ ] **Step 3: Run — expect failure**

```bash
pytest tests/test_parser.py -k "test_extracts or test_import or test_function or test_method or test_stable or test_node_hash or test_no_doc" -v
```

Expected: all FAIL with ImportError or AttributeError.

- [ ] **Step 4: Implement `services/parser/app/extractor.py` — node extraction**

```python
from __future__ import annotations

import hashlib
from typing import Optional
from tree_sitter import Node

from .models import FileChangedEvent, IntraFileEdge, ParsedNode


# ── Hash helpers ──────────────────────────────────────────────────────────────

def _sha256(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def stable_id(repo: str, file_path: str, node_type: str, qualified_name: str) -> str:
    return _sha256(f"{repo}:{file_path}:{node_type}:{qualified_name}")


def node_hash(name: str, signature: str, body: str) -> str:
    return _sha256(name + signature + body)


# ── Source text helpers ────────────────────────────────────────────────────────

def _text(source: bytes, node: Node) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8")


# ── Docstring extraction ───────────────────────────────────────────────────────

def _get_docstring(body_node: Optional[Node], source: bytes) -> str:
    """Return the first string literal in a block node, or ''."""
    if body_node is None:
        return ""
    for child in body_node.named_children:
        if child.type == "expression_statement":
            for expr in child.named_children:
                if expr.type == "string":
                    # Prefer string_content child (strips quotes automatically)
                    for sc in expr.named_children:
                        if sc.type == "string_content":
                            return _text(source, sc)
                    # Fallback: strip outer quote markers manually
                    raw = _text(source, expr)
                    for q in ('"""', "'''", '"', "'"):
                        if raw.startswith(q) and raw.endswith(q) and len(raw) >= 2 * len(q):
                            return raw[len(q):-len(q)]
                    return raw
        elif child.type == "comment":
            continue
        else:
            break
    return ""


# ── Signature reconstruction ──────────────────────────────────────────────────

def _get_signature(fn_node: Node, source: bytes, name: str) -> str:
    """Reconstruct function signature: name(params) -> return_type."""
    params_node = fn_node.child_by_field_name("parameters")
    return_node = fn_node.child_by_field_name("return_type")

    params_text = _text(source, params_node) if params_node else "()"
    sig = name + params_text

    if return_node:
        sig += " -> " + _text(source, return_node)

    return sig


# ── Import name extraction ────────────────────────────────────────────────────

def _get_import_name(node: Node, source: bytes) -> str:
    if node.type == "import_statement":
        name_node = node.child_by_field_name("name")
        if name_node:
            # aliased_import: pick the dotted_name inside it
            if name_node.type == "aliased_import":
                inner = name_node.child_by_field_name("name")
                return _text(source, inner) if inner else _text(source, name_node)
            return _text(source, name_node)
    elif node.type == "import_from_statement":
        module_node = node.child_by_field_name("module_name")
        if module_node:
            return _text(source, module_node)
    return _text(source, node)


# ── CALLS edge detection ──────────────────────────────────────────────────────

def _find_call_names(fn_node: Node) -> list[str]:
    """Walk AST for call nodes; return list of called names (bare or self.method)."""
    names: list[str] = []

    def walk(n: Node) -> None:
        if n.type == "call":
            func = n.child_by_field_name("function")
            if func:
                if func.type == "identifier":
                    names.append(func.text.decode("utf-8"))
                elif func.type == "attribute":
                    obj = func.child_by_field_name("object")
                    attr = func.child_by_field_name("attribute")
                    if obj and attr and obj.text == b"self":
                        names.append(attr.text.decode("utf-8"))
        for child in n.children:
            walk(child)

    walk(fn_node)
    return names


# ── Main extraction ────────────────────────────────────────────────────────────

def extract(
    root: Node,
    source: bytes,
    repo: str,
    file_path: str,
) -> tuple[list[ParsedNode], list[IntraFileEdge]]:
    """Walk the AST and return (nodes, edges) for all top-level constructs."""
    nodes: list[ParsedNode] = []
    # (parsed_node, ast_node) for functions/methods — used in edge pass
    fn_pairs: list[tuple[ParsedNode, Node]] = []
    # name → stable_id for intra-file edge resolution
    name_to_sid: dict[str, str] = {}

    for child in root.named_children:
        # ── Imports ──────────────────────────────────────────────────────────
        if child.type in ("import_statement", "import_from_statement"):
            name = _get_import_name(child, source)
            sid = stable_id(repo, file_path, "import", name)
            body = _text(source, child)
            nodes.append(ParsedNode(
                stable_id=sid,
                node_hash=node_hash(name, "", body),
                type="import",
                name=name,
                signature="",
                docstring="",
                body=body,
                line_start=child.start_point[0] + 1,
                line_end=child.end_point[0] + 1,
            ))

        # ── Classes ───────────────────────────────────────────────────────────
        elif child.type == "class_definition":
            cls_name_node = child.child_by_field_name("name")
            cls_name = _text(source, cls_name_node)
            cls_sid = stable_id(repo, file_path, "class", cls_name)
            cls_body_node = child.child_by_field_name("body")
            cls_docstring = _get_docstring(cls_body_node, source)
            cls_body_text = _text(source, child)

            nodes.append(ParsedNode(
                stable_id=cls_sid,
                node_hash=node_hash(cls_name, "", cls_body_text),
                type="class",
                name=cls_name,
                signature="",
                docstring=cls_docstring,
                body=cls_body_text,
                line_start=child.start_point[0] + 1,
                line_end=child.end_point[0] + 1,
            ))
            name_to_sid[cls_name] = cls_sid

            # Methods
            if cls_body_node:
                for method in cls_body_node.named_children:
                    if method.type == "function_definition":
                        m_name_node = method.child_by_field_name("name")
                        m_name = _text(source, m_name_node)
                        m_qname = f"{cls_name}.{m_name}"
                        m_sid = stable_id(repo, file_path, "method", m_qname)
                        m_sig = _get_signature(method, source, m_name)
                        m_body_node = method.child_by_field_name("body")
                        m_docstring = _get_docstring(m_body_node, source)
                        m_body_text = _text(source, method)

                        pn = ParsedNode(
                            stable_id=m_sid,
                            node_hash=node_hash(m_name, m_sig, m_body_text),
                            type="method",
                            name=m_name,
                            signature=m_sig,
                            docstring=m_docstring,
                            body=m_body_text,
                            line_start=method.start_point[0] + 1,
                            line_end=method.end_point[0] + 1,
                            parent_id=cls_sid,
                        )
                        nodes.append(pn)
                        fn_pairs.append((pn, method))
                        name_to_sid[m_name] = m_sid
                        name_to_sid[m_qname] = m_sid

        # ── Top-level functions ───────────────────────────────────────────────
        elif child.type == "function_definition":
            fn_name_node = child.child_by_field_name("name")
            fn_name = _text(source, fn_name_node)
            fn_sid = stable_id(repo, file_path, "function", fn_name)
            fn_sig = _get_signature(child, source, fn_name)
            fn_body_node = child.child_by_field_name("body")
            fn_docstring = _get_docstring(fn_body_node, source)
            fn_body_text = _text(source, child)

            pn = ParsedNode(
                stable_id=fn_sid,
                node_hash=node_hash(fn_name, fn_sig, fn_body_text),
                type="function",
                name=fn_name,
                signature=fn_sig,
                docstring=fn_docstring,
                body=fn_body_text,
                line_start=child.start_point[0] + 1,
                line_end=child.end_point[0] + 1,
            )
            nodes.append(pn)
            fn_pairs.append((pn, child))
            name_to_sid[fn_name] = fn_sid

    # ── CALLS edges ───────────────────────────────────────────────────────────
    edges: list[IntraFileEdge] = []
    for pn, ast_node in fn_pairs:
        for called_name in _find_call_names(ast_node):
            target_sid = name_to_sid.get(called_name)
            if target_sid and target_sid != pn.stable_id:
                edges.append(IntraFileEdge(
                    source_stable_id=pn.stable_id,
                    target_stable_id=target_sid,
                    type="CALLS",
                ))

    return nodes, edges


# ── diff_type filtering ────────────────────────────────────────────────────────

def apply_diff_filter(
    nodes: list[ParsedNode],
    edges: list[IntraFileEdge],
    event: FileChangedEvent,
) -> tuple[list[ParsedNode], list[IntraFileEdge]]:
    """Filter nodes and edges based on diff_type."""
    if event.diff_type in ("full_rescan", "added"):
        return nodes, edges
    elif event.diff_type == "deleted":
        return [], []
    elif event.diff_type == "modified":
        emit_set = set(event.changed_nodes) | set(event.added_nodes)
        filtered_nodes = [n for n in nodes if n.stable_id in emit_set]
        filtered_sids = {n.stable_id for n in filtered_nodes}
        filtered_edges = [e for e in edges if e.source_stable_id in filtered_sids]
        return filtered_nodes, filtered_edges
    else:
        # Unknown diff_type — emit all
        return nodes, edges
```

- [ ] **Step 5: Run node extraction tests — expect pass**

```bash
pytest tests/test_parser.py -k "test_extracts or test_import or test_function or test_method or test_stable or test_node_hash or test_no_doc or test_method_doc" -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add services/parser/app/extractor.py services/parser/tests/fixtures/sample.py services/parser/tests/test_parser.py
git commit -m "feat(parser): add extractor — node extraction, CALLS edges, diff filtering"
```

---

## Task 5: diff_type Filtering Tests

**Files:**
- Modify: `services/parser/tests/test_parser.py`

The `apply_diff_filter` is already implemented. Now add the required tests and verify they pass.

- [ ] **Step 1: Add diff_type tests**

Append to `tests/test_parser.py`:

```python
# ── Extractor — diff_type filtering ──────────────────────────────────────────

def _make_event(**kwargs) -> "FileChangedEvent":
    from app.models import FileChangedEvent
    defaults = dict(
        repo="repo", commit_sha="abc", path="auth/service.py",
        language="python", diff_type="full_rescan",
        changed_nodes=[], added_nodes=[], deleted_nodes=[],
    )
    defaults.update(kwargs)
    return FileChangedEvent(**defaults)


def test_full_rescan_emits_all_nodes():
    from app.parser import parse
    from app.extractor import extract, apply_diff_filter
    source = (FIXTURES / "sample.py").read_bytes()
    nodes, edges = extract(parse(source, "python"), source, "repo", "auth/service.py")
    event = _make_event(diff_type="full_rescan")
    fn, fe = apply_diff_filter(nodes, edges, event)
    assert len(fn) == len(nodes)
    assert len(fe) == len(edges)


def test_added_emits_all_nodes():
    from app.parser import parse
    from app.extractor import extract, apply_diff_filter
    source = (FIXTURES / "sample.py").read_bytes()
    nodes, edges = extract(parse(source, "python"), source, "repo", "auth/service.py")
    event = _make_event(diff_type="added")
    fn, fe = apply_diff_filter(nodes, edges, event)
    assert len(fn) == len(nodes)
    assert len(fe) == len(edges)


def test_modified_filters_nodes_and_edges():
    from app.parser import parse
    from app.extractor import extract, apply_diff_filter
    source = (FIXTURES / "sample.py").read_bytes()
    nodes, edges = extract(parse(source, "python"), source, "repo", "auth/service.py")
    target = nodes[0]
    event = _make_event(diff_type="modified", changed_nodes=[target.stable_id])
    fn, fe = apply_diff_filter(nodes, edges, event)
    assert all(n.stable_id == target.stable_id for n in fn)
    assert all(e.source_stable_id == target.stable_id for e in fe)


def test_deleted_emits_empty_nodes_and_forwards_deleted_nodes():
    from app.parser import parse
    from app.extractor import extract, apply_diff_filter
    from app.models import ParsedFileEvent
    source = (FIXTURES / "sample.py").read_bytes()
    nodes, edges = extract(parse(source, "python"), source, "repo", "auth/service.py")
    deleted = ["sha256:aaa", "sha256:bbb"]
    event = _make_event(diff_type="deleted", deleted_nodes=deleted)
    fn, fe = apply_diff_filter(nodes, edges, event)
    assert fn == []
    assert fe == []
    # deleted_nodes are forwarded at the event level (consumer responsibility)
    # verify the event carries them
    out = ParsedFileEvent(
        file_path=event.path, language=event.language, repo=event.repo,
        commit_sha=event.commit_sha, nodes=fn, intra_file_edges=fe,
        deleted_nodes=event.deleted_nodes,
    )
    assert out.deleted_nodes == deleted
```

- [ ] **Step 2: Run — expect pass**

```bash
pytest tests/test_parser.py -k "test_full_rescan or test_added or test_modified or test_deleted" -v
```

Expected: 4 tests PASS.

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/test_parser.py -v
```

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add services/parser/tests/test_parser.py
git commit -m "test(parser): add diff_type filtering tests"
```

---

## Task 6: FastAPI App

**Files:**
- Create: `services/parser/app/main.py`
- Modify: `services/parser/tests/test_parser.py`

- [ ] **Step 1: Add failing endpoint tests**

Append to `tests/test_parser.py`:

```python
# ── FastAPI endpoints ──────────────────────────────────────────────────────────

import os
from unittest.mock import patch, MagicMock


def _make_test_client():
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def test_health_returns_200():
    import asyncio
    async def _run():
        async with _make_test_client() as client:
            r = await client.get("/health")
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "ok"
            assert body["service"] == "parser"
            assert body["version"] == "0.1.0"
    asyncio.run(_run())


def test_ready_returns_503_when_redis_unreachable():
    import asyncio
    async def _run():
        with patch("app.main._get_redis") as mock_redis:
            mock_redis.return_value.ping.side_effect = Exception("refused")
            async with _make_test_client() as client:
                r = await client.get("/ready")
                assert r.status_code == 503
    asyncio.run(_run())


def test_ready_returns_200_when_redis_reachable():
    import asyncio
    async def _run():
        with patch("app.main._get_redis") as mock_redis:
            mock_redis.return_value.ping.return_value = True
            async with _make_test_client() as client:
                r = await client.get("/ready")
                assert r.status_code == 200
    asyncio.run(_run())
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_parser.py -k "test_health or test_ready" -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `services/parser/app/main.py`**

```python
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import redis as redis_lib
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "parser"

_redis_client: redis_lib.Redis | None = None


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = redis_lib.from_url(url)
    return _redis_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .consumer import run_consumer
    task = asyncio.create_task(run_consumer())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE, "version": VERSION}


@app.get("/ready")
def ready():
    try:
        _get_redis().ping()
        return {"status": "ok"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable"})


@app.get("/metrics")
def metrics():
    # Stub: returns hardcoded zeros. Wire real counters when metrics matter.
    lines = [
        "# HELP parser_messages_processed_total Total messages processed",
        "# TYPE parser_messages_processed_total counter",
        "parser_messages_processed_total 0",
        "# HELP parser_messages_failed_total Total messages failed",
        "# TYPE parser_messages_failed_total counter",
        "parser_messages_failed_total 0",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
```

- [ ] **Step 4: Run — expect pass**

```bash
pytest tests/test_parser.py -k "test_health or test_ready" -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest tests/test_parser.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add services/parser/app/main.py services/parser/tests/test_parser.py
git commit -m "feat(parser): add FastAPI app with /health /ready /metrics"
```

---

## Task 7: Redis Consumer

**Files:**
- Create: `services/parser/app/consumer.py`

No unit tests for the consumer loop (requires live Redis). The loop is covered by integration verification in the DoD checklist. Focus is on a clean, correct implementation.

- [ ] **Step 1: Implement `services/parser/app/consumer.py`**

```python
import asyncio
import logging
import os
import socket

import redis
import redis.exceptions

from .extractor import apply_diff_filter, extract
from .models import FileChangedEvent, ParsedFileEvent
from .parser import parse

logger = logging.getLogger(__name__)

STREAM_IN = "stream:file-changed"
STREAM_OUT = "stream:parsed-file"
GROUP = "parser-group"


def _get_redis() -> redis.Redis:
    return redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))


def _process(r: redis.Redis, data: dict) -> None:
    raw = data.get(b"event") or data.get("event") or b"{}"
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    event = FileChangedEvent.model_validate_json(raw)

    repo_root = os.environ.get("REPO_ROOT", "/repos")

    if event.diff_type == "deleted":
        out = ParsedFileEvent(
            file_path=event.path,
            language=event.language,
            repo=event.repo,
            commit_sha=event.commit_sha,
            nodes=[],
            intra_file_edges=[],
            deleted_nodes=event.deleted_nodes,
        )
    else:
        file_path = os.path.join(repo_root, event.path)
        with open(file_path, "rb") as fh:
            source_bytes = fh.read()

        root = parse(source_bytes, event.language)
        all_nodes, all_edges = extract(root, source_bytes, event.repo, event.path)
        filtered_nodes, filtered_edges = apply_diff_filter(all_nodes, all_edges, event)

        out = ParsedFileEvent(
            file_path=event.path,
            language=event.language,
            repo=event.repo,
            commit_sha=event.commit_sha,
            nodes=filtered_nodes,
            intra_file_edges=filtered_edges,
            deleted_nodes=event.deleted_nodes,
        )

    r.xadd(STREAM_OUT, {"event": out.model_dump_json()})


def _consumer_loop() -> None:
    r = _get_redis()
    consumer_name = f"parser-{socket.gethostname()}"

    try:
        r.xgroup_create(STREAM_IN, GROUP, id="0", mkstream=True)
    except redis.exceptions.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise

    logger.info("Consumer started: group=%s consumer=%s", GROUP, consumer_name)

    while True:
        try:
            messages = r.xreadgroup(
                groupname=GROUP,
                consumername=consumer_name,
                streams={STREAM_IN: ">"},
                count=10,
                block=1000,
            )
            for _stream, events in (messages or []):
                for msg_id, data in events:
                    try:
                        _process(r, data)
                        r.xack(STREAM_IN, GROUP, msg_id)
                    except Exception as exc:
                        logger.error("Failed msg_id=%s: %s", msg_id, exc)
        except Exception as exc:
            logger.error("Consumer loop error: %s", exc)


async def run_consumer() -> None:
    """Run the blocking consumer loop in a thread so it doesn't block the event loop.

    Note: cancelling this coroutine will not stop the background thread immediately —
    _consumer_loop runs until the process exits. This is acceptable for this service.
    """
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _consumer_loop)
    except asyncio.CancelledError:
        pass
```

- [ ] **Step 2: Verify imports resolve**

```bash
cd services/parser
python -c "from app.consumer import _process; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add services/parser/app/consumer.py
git commit -m "feat(parser): add Redis Stream consumer loop"
```

---

## Task 8: Dockerfile

**Files:**
- Create: `services/parser/Dockerfile`

- [ ] **Step 1: Create `services/parser/Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY requirements.txt ./
RUN uv pip install --system -r requirements.txt

COPY app/ app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

- [ ] **Step 2: Build image**

```bash
cd services/parser
docker build -t tersecontext-parser .
```

Expected: build completes with no errors. Final line: `Successfully built ...` or `writing image sha256:...`

- [ ] **Step 3: Verify health endpoint**

```bash
docker run -d --name parser-test \
  -e REDIS_URL=redis://host.docker.internal:6379 \
  -e REPO_ROOT=/repos \
  -p 8081:8080 tersecontext-parser

sleep 3
curl -s http://localhost:8081/health
```

Expected: `{"status":"ok","service":"parser","version":"0.1.0"}`

- [ ] **Step 4: Verify /ready returns 503 when Redis unreachable**

```bash
# The container has no Redis — /ready should return 503
curl -s -o /dev/null -w "%{http_code}" http://localhost:8081/ready
```

Expected: `503`

- [ ] **Step 5: Clean up**

```bash
docker stop parser-test && docker rm parser-test
```

- [ ] **Step 6: Commit**

```bash
git add services/parser/Dockerfile
git commit -m "feat(parser): add Dockerfile"
```

---

## Task 9: Final Verification

**Files:**
- Read: `docs/superpowers/specs/2026-03-27-parser-service-design.md` (Definition of Done)

- [ ] **Step 1: Run full test suite**

```bash
cd services/parser
pytest tests/ -v
```

Expected: all tests PASS. Zero failures.

- [ ] **Step 2: Verify CALLS edge in sample**

```bash
python -c "
from app.parser import parse
from app.extractor import extract
source = open('tests/fixtures/sample.py', 'rb').read()
root = parse(source, 'python')
nodes, edges = extract(root, source, 'repo', 'auth/service.py')
print('nodes:', [(n.type, n.name) for n in nodes])
print('edges:', [(e.source_stable_id[:20], e.target_stable_id[:20]) for e in edges])
"
```

Expected: `authenticate` method has a CALLS edge targeting `_hash_password`.

- [ ] **Step 3: Confirm Definition of Done**

Check each item:
- [x] Service starts and `/health` returns 200 (verified in Task 8)
- [x] `/ready` returns 503 when Redis unreachable (verified in Task 8)
- [x] Parsing `sample.py` produces correctly shaped `ParsedFile` event (test_extracts_* tests)
- [x] `stable_id` is deterministic (test_stable_id_is_deterministic)
- [x] `node_hash` changes when body changes (test_node_hash_changes_when_body_changes)
- [x] Methods have `parent_id` pointing to class (test_extracts_method_nodes_with_parent_id)
- [x] `diff_type=modified` only emits changed/added nodes (test_modified_filters_nodes_and_edges)
- [x] `diff_type=deleted` emits empty nodes[] (test_deleted_emits_empty_nodes_and_forwards_deleted_nodes)
- [x] All unit tests pass
- [x] Dockerfile builds cleanly

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat(parser): complete parser service — all tests passing"
```
