# Symbol Resolver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the symbol-resolver service, which consumes `stream:parsed-file`, resolves import nodes to Neo4j targets, and writes IMPORTS edges — completing the cross-file connection layer of the knowledge graph.

**Architecture:** FastAPI service with a single Redis stream consumer (group `symbol-resolver-group`) on `stream:parsed-file`. For each import node in a parsed file, it either writes an IMPORTS edge to Neo4j (for resolvable targets) or pushes to a pending_refs queue (Redis list) for retry when the target is later indexed. External package imports (`import X`) always create Package nodes in Neo4j.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, pydantic v2, redis-py (async), neo4j driver (sync, run_in_executor), `ast` stdlib for import parsing.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `services/symbol-resolver/requirements.txt` | Create | Dependencies |
| `services/symbol-resolver/Dockerfile` | Create | Container |
| `services/symbol-resolver/app/__init__.py` | Create | Python package marker |
| `services/symbol-resolver/app/models.py` | Create | ParsedFileEvent, PendingRef |
| `services/symbol-resolver/app/neo4j_client.py` | Create | Driver factory |
| `services/symbol-resolver/app/resolver.py` | Create | Symbol extraction, Neo4j lookups, edge writes |
| `services/symbol-resolver/app/pending.py` | Create | Push/retry/expire pending_refs queue |
| `services/symbol-resolver/app/consumer.py` | Create | Redis stream consumer loop |
| `services/symbol-resolver/app/main.py` | Create | FastAPI app, lifespan, /health /ready /metrics |
| `services/symbol-resolver/tests/__init__.py` | Create | Test package marker |
| `services/symbol-resolver/tests/test_resolver.py` | Create | All 12 unit tests |
| `docker-compose.yml` | Modify | Add symbol-resolver service entry |

**Reference files to read before implementing** (do not modify):
- `services/graph-writer/app/main.py` — FastAPI + lifespan + health/ready/metrics pattern
- `services/graph-writer/app/consumer.py` — Redis xreadgroup + xack pattern
- `services/graph-writer/app/models.py` — ParsedFileEvent shape to copy
- `services/graph-writer/Dockerfile` — Dockerfile pattern to mirror exactly

---

## Task 1: Scaffold — directories, requirements, Dockerfile

**Files:**
- Create: `services/symbol-resolver/requirements.txt`
- Create: `services/symbol-resolver/Dockerfile`
- Create: `services/symbol-resolver/app/__init__.py`
- Create: `services/symbol-resolver/tests/__init__.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p services/symbol-resolver/app services/symbol-resolver/tests
```

- [ ] **Step 2: Create requirements.txt**

```
services/symbol-resolver/requirements.txt
```
```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
redis>=5.0.0
pydantic>=2.0.0
neo4j>=5.0.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 3: Create Dockerfile** (mirrors `services/graph-writer/Dockerfile` exactly)

```
services/symbol-resolver/Dockerfile
```
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

- [ ] **Step 4: Create empty package markers**

```
services/symbol-resolver/app/__init__.py
```
(empty file)

```
services/symbol-resolver/tests/__init__.py
```
(empty file)

- [ ] **Step 5: Commit**

```bash
git add services/symbol-resolver/
git commit -m "chore(symbol-resolver): scaffold service structure"
```

---

## Task 2: Models

**Files:**
- Create: `services/symbol-resolver/app/models.py`

- [ ] **Step 1: Create models.py**

```python
# services/symbol-resolver/app/models.py
from __future__ import annotations
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ParsedNode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stable_id: str
    type: str
    name: str
    qualified_name: str = ""
    body: str


class ParsedFileEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repo: str
    commit_sha: str
    file_path: str
    language: str
    nodes: list[ParsedNode]


class PendingRef(BaseModel):
    source_stable_id: str   # stable_id of the import node (type="import")
    target_name: str        # imported symbol name (e.g. "AuthService")
    path_hint: str          # slash-separated path for fallback query (e.g. "auth/service")
    edge_type: str = "IMPORTS"
    repo: str
    attempted_at: datetime
    attempt_count: int = 0
```

- [ ] **Step 2: Verify models import cleanly**

```bash
cd services/symbol-resolver && pip install -r requirements.txt -q && python -c "from app.models import ParsedFileEvent, PendingRef; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add services/symbol-resolver/app/models.py
git commit -m "feat(symbol-resolver): add data models"
```

---

## Task 3: resolver.py — symbol extraction and path hints (tests 1–3, 12)

**Files:**
- Create: `services/symbol-resolver/app/resolver.py` (partial — extraction functions only)
- Create: `services/symbol-resolver/tests/test_resolver.py` (first 4 tests)

- [ ] **Step 1: Write failing tests for symbol extraction**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/symbol-resolver && pytest tests/test_resolver.py::test_extract_symbols_from_statement tests/test_resolver.py::test_extract_symbols_multi tests/test_resolver.py::test_extract_symbols_aliased tests/test_resolver.py::test_relative_import_path_hint -v 2>&1 | head -20
```

Expected: `ERROR` or `ImportError` — `resolver` does not exist yet.

- [ ] **Step 3: Create resolver.py with parse_import_body and compute_path_hint**

```python
# services/symbol-resolver/app/resolver.py
from __future__ import annotations
import ast
import logging
from datetime import datetime, timezone, timedelta
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)


# ── Import parsing ─────────────────────────────────────────────────────────────

def parse_import_body(body: str) -> dict:
    """
    Parse an import statement and return a dict describing it.

    Returns one of:
      {"kind": "from",  "module": "auth.service", "dots": 0, "names": ["AuthService"]}
      {"kind": "plain", "names": ["os"]}   — for bare 'import X' statements

    For aliased imports ('from x import Y as Z'), names contains Y (.name), not Z (.asname).
    For multi-module plain imports ('import os, sys'), names contains both.
    For multi-symbol from-imports ('from x import A, B'), names contains both.
    """
    try:
        tree = ast.parse(body.strip(), mode="single")
    except SyntaxError:
        logger.warning("Could not parse import body: %r", body)
        return {"kind": "plain", "names": []}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names = [alias.name for alias in node.names]
            return {
                "kind": "from",
                "module": node.module or "",
                "dots": node.level,
                "names": names,
            }
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
            return {"kind": "plain", "names": names}

    return {"kind": "plain", "names": []}


def compute_path_hint(module: str, file_path: str, dots: int) -> str:
    """
    Convert a module name and relative-import level into a slash-form path hint
    suitable for Neo4j CONTAINS queries.

    dots=0  (absolute): "auth.service"  → "auth/service"
    dots=1  (relative): strip filename, use parent dir. "auth/service.py" → "auth/"
    dots=2+: strip filename + (dots-1) more components.
    """
    if dots == 0:
        return module.replace(".", "/")

    # Relative import: start from the importing file's directory.
    parts = PurePosixPath(file_path).parts  # e.g. ("auth", "service.py")
    # Strip filename
    dir_parts = list(parts[:-1])
    # Strip (dots - 1) more directory components for each additional dot
    for _ in range(dots - 1):
        if dir_parts:
            dir_parts.pop()
    if not dir_parts:
        return ""
    return "/".join(dir_parts) + "/"
```

- [ ] **Step 4: Run extraction tests — expect PASS**

```bash
cd services/symbol-resolver && pytest tests/test_resolver.py::test_extract_symbols_from_statement tests/test_resolver.py::test_extract_symbols_multi tests/test_resolver.py::test_extract_symbols_aliased tests/test_resolver.py::test_relative_import_path_hint tests/test_resolver.py::test_relative_import_path_hint_double_dot tests/test_resolver.py::test_plain_import_path_hint -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/symbol-resolver/app/resolver.py services/symbol-resolver/tests/test_resolver.py
git commit -m "feat(symbol-resolver): add import body parser and path hint computation"
```

---

## Task 4: resolver.py — Neo4j edge writes (tests 4–8)

**Files:**
- Modify: `services/symbol-resolver/app/resolver.py` (add Neo4j query constants + write functions)
- Modify: `services/symbol-resolver/tests/test_resolver.py` (add tests 4–8)

The Neo4j driver follows the same session-context-manager pattern as graph-writer. Queries that do reads return `.data()` (list of dicts). To detect whether a MATCH-based write succeeded (i.e., both nodes existed), append `RETURN count(r) AS c` to the query — if the list is empty, a MATCH found nothing.

- [ ] **Step 1: Write failing tests for Neo4j writes**

Append to `services/symbol-resolver/tests/test_resolver.py`. Add the new imports at the **top of the file** alongside the existing imports, then append the test functions at the bottom:

```python
from unittest.mock import MagicMock, call
from app.resolver import resolve_import, write_package_edge


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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/symbol-resolver && pytest tests/test_resolver.py::test_external_package_import tests/test_resolver.py::test_exact_match_writes_imports_edge tests/test_resolver.py::test_path_hint_fallback tests/test_resolver.py::test_both_lookups_fail_pushes_pending tests/test_resolver.py::test_importer_not_yet_in_neo4j_pushes_pending -v 2>&1 | head -20
```

Expected: `ImportError` — functions not defined yet.

- [ ] **Step 3: Add Neo4j constants and write functions to resolver.py**

Append to `services/symbol-resolver/app/resolver.py` (after the existing parse functions):

```python
# ── Neo4j query constants ──────────────────────────────────────────────────────

LOOKUP_EXACT_QUERY = """
MATCH (n:Node {repo: $repo, qualified_name: $name, active: true})
RETURN n.stable_id AS stable_id LIMIT 1
"""

LOOKUP_PATH_QUERY = """
MATCH (n:Node {repo: $repo, name: $name, active: true})
WHERE n.file_path CONTAINS $path_hint
RETURN n.stable_id AS stable_id LIMIT 1
"""

WRITE_IMPORT_EDGE_QUERY = """
MATCH (a:Node {stable_id: $importer_id})
MATCH (b:Node {stable_id: $imported_id})
MERGE (a)-[r:IMPORTS]->(b)
SET r.source = 'static', r.updated_at = datetime()
RETURN count(r) AS c
"""

WRITE_PACKAGE_EDGE_QUERY = """
MATCH (a:Node {stable_id: $importer_id})
MERGE (pkg:Package {name: $pkg_name, repo: $repo})
MERGE (a)-[r:IMPORTS]->(pkg)
SET r.source = 'static', r.updated_at = datetime()
RETURN count(r) AS c
"""


# ── Neo4j write functions ──────────────────────────────────────────────────────

def resolve_import(driver, importer_stable_id: str, target_name: str, path_hint: str, repo: str) -> bool:
    """
    Attempt to write an IMPORTS edge from the import node to the target symbol.

    Strategy:
      1. Try exact qualified_name lookup in Neo4j.
      2. If miss, try name + file_path CONTAINS path_hint.
      3. If target found, write the edge. Returns True if edge written, False otherwise.

    Returns False if:
      - target not found (both lookups miss)
      - importer node not yet in Neo4j (MATCH in write query returns 0 rows)
    """
    with driver.session() as session:
        # Attempt 1: exact qualified_name match
        rows = session.run(LOOKUP_EXACT_QUERY, repo=repo, name=target_name).data()
        if not rows and path_hint:
            # Attempt 2: name + file path hint
            rows = session.run(LOOKUP_PATH_QUERY, repo=repo, name=target_name, path_hint=path_hint).data()

        if not rows:
            return False

        imported_id = rows[0]["stable_id"]
        result = session.run(
            WRITE_IMPORT_EDGE_QUERY,
            importer_id=importer_stable_id,
            imported_id=imported_id,
        ).data()
        return bool(result)


def write_package_edge(driver, importer_stable_id: str, pkg_name: str, repo: str) -> bool:
    """
    MERGE a Package node and write an IMPORTS edge from the import node to it.
    Returns True if the edge was written (importer node existed), False otherwise.
    Package nodes: no embed_text or vectors.
    """
    with driver.session() as session:
        result = session.run(
            WRITE_PACKAGE_EDGE_QUERY,
            importer_id=importer_stable_id,
            pkg_name=pkg_name,
            repo=repo,
        ).data()
        return bool(result)
```

- [ ] **Step 4: Run Neo4j write tests — expect PASS**

```bash
cd services/symbol-resolver && pytest tests/test_resolver.py::test_external_package_import tests/test_resolver.py::test_exact_match_writes_imports_edge tests/test_resolver.py::test_path_hint_fallback tests/test_resolver.py::test_both_lookups_fail_pushes_pending tests/test_resolver.py::test_importer_not_yet_in_neo4j_pushes_pending -v
```

Expected: all PASS.

- [ ] **Step 5: Run all tests so far**

```bash
cd services/symbol-resolver && pytest tests/ -v
```

Expected: all PASS (tests 1–8 plus path-hint tests).

- [ ] **Step 6: Commit**

```bash
git add services/symbol-resolver/app/resolver.py services/symbol-resolver/tests/test_resolver.py
git commit -m "feat(symbol-resolver): add Neo4j lookup and edge write functions"
```

---

## Task 5: pending.py — push/retry/expire (tests 9–11)

**Files:**
- Create: `services/symbol-resolver/app/pending.py`
- Modify: `services/symbol-resolver/tests/test_resolver.py` (add tests 9–11)

The pending_refs queue is a Redis list at key `pending_refs:{repo}`. RPUSH to add. Retry uses LRANGE (read all) + DEL (clear) + RPUSH (put back unresolved). `attempt_count` is incremented before writing back.

Expiry: `attempt_count > 5` OR `attempted_at` older than 24h → log and drop.

- [ ] **Step 1: Write failing tests for pending.py**

Append to `services/symbol-resolver/tests/test_resolver.py`:

```python
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch
from app.pending import push_pending, retry_pending
from app.models import PendingRef


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
    r.lrange.return_value = [_ref_json(ref)]

    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    # resolve_import will succeed: exact lookup finds target, edge write succeeds
    session.run.return_value.data.side_effect = [
        [{"stable_id": "target-sid"}],  # exact lookup
        [{"c": 1}],                      # edge write
    ]

    retry_pending(driver, r, "myrepo")

    r.lrange.assert_called_once_with("pending_refs:myrepo", 0, -1)
    r.delete.assert_called_once_with("pending_refs:myrepo")
    # Ref was resolved — should NOT be pushed back
    r.rpush.assert_not_called()


# ── Test 10: retry resolves because importer now exists ───────────────────────

def test_pending_retry_resolves_on_importer_arrival():
    # First call: importer existed, target found — edge write succeeds
    ref = _make_ref(attempt_count=1)
    r = MagicMock()
    r.lrange.return_value = [_ref_json(ref)]

    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.return_value.data.side_effect = [
        [{"stable_id": "target-sid"}],  # exact lookup finds target
        [{"c": 1}],                      # edge write now succeeds (importer arrived)
    ]

    retry_pending(driver, r, "myrepo")

    r.delete.assert_called_once_with("pending_refs:myrepo")
    r.rpush.assert_not_called()


# ── Test 11a: expiry by attempt_count ─────────────────────────────────────────

def test_pending_expiry_drops_old_ref_by_count():
    ref = _make_ref(attempt_count=6)  # > 5 → drop
    r = MagicMock()
    r.lrange.return_value = [_ref_json(ref)]

    driver = MagicMock()
    retry_pending(driver, r, "myrepo")

    r.delete.assert_called_once_with("pending_refs:myrepo")
    r.rpush.assert_not_called()
    driver.session.assert_not_called()  # no Neo4j call attempted


# ── Test 11b: expiry by age ───────────────────────────────────────────────────

def test_pending_expiry_drops_old_ref_by_age():
    old_time = datetime.now(timezone.utc) - timedelta(hours=25)
    ref = _make_ref(attempted_at=old_time, attempt_count=1)  # old but few attempts
    r = MagicMock()
    r.lrange.return_value = [_ref_json(ref)]

    driver = MagicMock()
    retry_pending(driver, r, "myrepo")

    r.rpush.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/symbol-resolver && pytest tests/test_resolver.py::test_pending_retry_resolves tests/test_resolver.py::test_pending_retry_resolves_on_importer_arrival tests/test_resolver.py::test_pending_expiry_drops_old_ref_by_count tests/test_resolver.py::test_pending_expiry_drops_old_ref_by_age -v 2>&1 | head -20
```

Expected: `ImportError` — `pending` module not found.

- [ ] **Step 3: Create pending.py**

```python
# services/symbol-resolver/app/pending.py
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

from .models import PendingRef
from .resolver import resolve_import

logger = logging.getLogger(__name__)

PENDING_TTL = timedelta(hours=24)
MAX_ATTEMPTS = 5


def push_pending(r, ref: PendingRef) -> None:
    """Push a PendingRef onto the pending_refs:{repo} Redis list."""
    r.rpush(f"pending_refs:{ref.repo}", ref.model_dump_json())


def _is_expired(ref: PendingRef) -> bool:
    """Return True if this ref should be dropped rather than retried."""
    age = datetime.now(timezone.utc) - ref.attempted_at
    return ref.attempt_count > MAX_ATTEMPTS or age > PENDING_TTL


def retry_pending(driver, r, repo: str) -> None:
    """
    Retry all pending refs for a repo.

    Bulk read-requeue pattern:
      1. LRANGE — read all
      2. DEL — clear the list
      3. For each ref: attempt resolution; push back if unresolved and not expired.

    attempt_count is incremented before writing back so the next cycle sees the
    updated count.
    """
    key = f"pending_refs:{repo}"
    raw_entries = r.lrange(key, 0, -1)
    if not raw_entries:
        return

    r.delete(key)

    for raw in raw_entries:
        try:
            ref = PendingRef.model_validate_json(raw)
        except Exception as exc:
            logger.warning("Malformed pending ref, dropping: %s", exc)
            continue

        if _is_expired(ref):
            logger.info(
                "Dropping expired pending ref: repo=%s target=%s attempts=%d",
                ref.repo, ref.target_name, ref.attempt_count,
            )
            continue

        resolved = resolve_import(
            driver,
            ref.source_stable_id,
            ref.target_name,
            ref.path_hint,
            ref.repo,
        )

        if resolved:
            logger.debug("Resolved pending ref: target=%s", ref.target_name)
        else:
            ref = ref.model_copy(update={
                "attempt_count": ref.attempt_count + 1,
                "attempted_at": datetime.now(timezone.utc),
            })
            push_pending(r, ref)
```

- [ ] **Step 4: Run pending tests — expect PASS**

```bash
cd services/symbol-resolver && pytest tests/test_resolver.py::test_pending_retry_resolves tests/test_resolver.py::test_pending_retry_resolves_on_importer_arrival tests/test_resolver.py::test_pending_expiry_drops_old_ref_by_count tests/test_resolver.py::test_pending_expiry_drops_old_ref_by_age -v
```

Expected: all PASS.

- [ ] **Step 5: Run full test suite**

```bash
cd services/symbol-resolver && pytest tests/ -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add services/symbol-resolver/app/pending.py services/symbol-resolver/tests/test_resolver.py
git commit -m "feat(symbol-resolver): add pending_refs queue (push/retry/expire)"
```

---

## Task 6: consumer.py — Redis stream consumer loop

**Files:**
- Create: `services/symbol-resolver/app/consumer.py`
- Create: `services/symbol-resolver/app/neo4j_client.py`

No unit tests for consumer.py (it's thin orchestration glue — integration tested via the full stack). Follow graph-writer's consumer.py pattern exactly.

- [ ] **Step 1: Create neo4j_client.py**

```python
# services/symbol-resolver/app/neo4j_client.py
import os
from neo4j import GraphDatabase


def make_driver():
    url = os.environ.get("NEO4J_URL", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    return GraphDatabase.driver(url, auth=(user, password))
```

- [ ] **Step 2: Create consumer.py**

```python
# services/symbol-resolver/app/consumer.py
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket

import redis as redis_sync
import redis.asyncio as aioredis
import redis.exceptions
from pydantic import ValidationError

from .models import ParsedFileEvent, PendingRef
from .pending import push_pending, retry_pending
from .resolver import compute_path_hint, parse_import_body, resolve_import, write_package_edge

logger = logging.getLogger(__name__)

STREAM = "stream:parsed-file"
GROUP = "symbol-resolver-group"


async def run_consumer(driver) -> None:
    """
    Main consumer coroutine. Uses two Redis clients:
      - r      (async, aioredis): for xgroup_create, xreadgroup, xack — must be non-blocking
      - r_sync (sync, redis):     passed to _process_event via run_in_executor for pending_refs
    """
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = aioredis.from_url(url)
    r_sync = redis_sync.from_url(url, decode_responses=False)
    consumer_name = f"symbol-resolver-{socket.gethostname()}"

    try:
        try:
            await r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        logger.info("Consumer started: group=%s consumer=%s", GROUP, consumer_name)
        loop = asyncio.get_running_loop()

        while True:
            try:
                messages = await r.xreadgroup(
                    groupname=GROUP,
                    consumername=consumer_name,
                    streams={STREAM: ">"},
                    count=10,
                    block=1000,
                )
                for _stream, events in (messages or []):
                    for msg_id, data in events:
                        try:
                            raw = data.get(b"event") or data.get("event")
                            if raw is None:
                                raise KeyError("missing 'event' key")
                            if isinstance(raw, bytes):
                                raw = raw.decode("utf-8")
                            event = ParsedFileEvent.model_validate_json(raw)

                            await loop.run_in_executor(None, _process_event, driver, r_sync, event)

                            await r.xack(STREAM, GROUP, msg_id)
                        except (json.JSONDecodeError, ValidationError, KeyError) as exc:
                            logger.warning("Bad message %s, skipping: %s", msg_id, exc)
                            await r.xack(STREAM, GROUP, msg_id)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.error("Consumer failed msg=%s: %s", msg_id, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Consumer loop error: %s", exc)
    finally:
        await r.aclose()
        r_sync.close()


def _process_event(driver, r, event: ParsedFileEvent) -> None:
    """
    Process a single ParsedFileEvent synchronously (called via run_in_executor).
    r is a sync Redis client; driver is a sync Neo4j driver.

    For each import node:
      - Plain 'import X' → write_package_edge (package race logged as warning, not retried)
      - 'from X import Y' → resolve_import; if unresolved, push_pending

    After processing all imports, retry pending_refs for this repo.
    """
    import_nodes = [n for n in event.nodes if n.type == "import"]

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    for node in import_nodes:
        info = parse_import_body(node.body)

        if info["kind"] == "plain":
            for pkg_name in info["names"]:
                if not write_package_edge(driver, node.stable_id, pkg_name, event.repo):
                    # Importer node not yet in Neo4j. Package edges are not retried in v1
                    # since external packages are never targets of other lookups.
                    logger.warning(
                        "Importer not yet in Neo4j for package edge %s → %s, skipping",
                        node.stable_id, pkg_name,
                    )
        else:
            # from-import
            path_hint = compute_path_hint(
                module=info["module"],
                file_path=event.file_path,
                dots=info["dots"],
            )
            for symbol in info["names"]:
                resolved = resolve_import(
                    driver,
                    node.stable_id,
                    symbol,
                    path_hint,
                    event.repo,
                )
                if not resolved:
                    ref = PendingRef(
                        source_stable_id=node.stable_id,
                        target_name=symbol,
                        path_hint=path_hint,
                        repo=event.repo,
                        attempted_at=now,
                    )
                    push_pending(r, ref)

    # Retry any pending refs — new nodes may now satisfy old unresolved imports
    retry_pending(driver, r, event.repo)
```

- [ ] **Step 3: Verify consumer imports cleanly**

```bash
cd services/symbol-resolver && python -c "from app.consumer import run_consumer; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add services/symbol-resolver/app/neo4j_client.py services/symbol-resolver/app/consumer.py
git commit -m "feat(symbol-resolver): add Redis stream consumer"
```

---

## Task 7: main.py — FastAPI app with health/ready/metrics

**Files:**
- Create: `services/symbol-resolver/app/main.py`

Mirror graph-writer's `main.py` exactly: lifespan starts the consumer task, `/health` returns static JSON, `/ready` pings Redis and Neo4j, `/metrics` returns Prometheus-format counters.

- [ ] **Step 1: Create main.py**

```python
# services/symbol-resolver/app/main.py
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

from .consumer import run_consumer
from .neo4j_client import make_driver

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "symbol-resolver"

_redis_client: aioredis.Redis | None = None
_driver = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = aioredis.from_url(url)
    return _redis_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _driver
    _driver = make_driver()
    task = asyncio.create_task(run_consumer(_driver))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if _driver:
            _driver.close()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE, "version": VERSION}


@app.get("/ready")
async def ready():
    errors = []
    try:
        await _get_redis().ping()
    except Exception as exc:
        errors.append(f"redis: {exc}")
    if _driver is None:
        errors.append("neo4j: driver not initialized")
    else:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _driver.verify_connectivity)
        except Exception as exc:
            errors.append(f"neo4j: {exc}")
    if errors:
        return JSONResponse(status_code=503, content={"status": "unavailable", "errors": errors})
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP symbol_resolver_messages_processed_total Total messages processed",
        "# TYPE symbol_resolver_messages_processed_total counter",
        "symbol_resolver_messages_processed_total 0",
        "# HELP symbol_resolver_imports_resolved_total Total IMPORTS edges written",
        "# TYPE symbol_resolver_imports_resolved_total counter",
        "symbol_resolver_imports_resolved_total 0",
        "# HELP symbol_resolver_pending_refs_total Total refs pushed to pending queue",
        "# TYPE symbol_resolver_pending_refs_total counter",
        "symbol_resolver_pending_refs_total 0",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
```

- [ ] **Step 2: Verify the app starts without import errors**

```bash
cd services/symbol-resolver && python -c "from app.main import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Run full test suite one more time**

```bash
cd services/symbol-resolver && pytest tests/ -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add services/symbol-resolver/app/main.py
git commit -m "feat(symbol-resolver): add FastAPI app with health/ready/metrics"
```

---

## Task 8: docker-compose integration

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add symbol-resolver entry to docker-compose.yml**

Open `docker-compose.yml` and add the following block after the `graph-writer` service entry (before `dual-retriever`):

```yaml
  symbol-resolver:
    build: ./services/symbol-resolver
    ports:
      - "8082:8080"
    environment:
      REDIS_URL: redis://redis:6379
      NEO4J_URL: bolt://neo4j:7687
      NEO4J_USER: neo4j
      NEO4J_PASSWORD: localpassword
    depends_on:
      redis:
        condition: service_healthy
      neo4j:
        condition: service_healthy
    networks:
      - tersecontext
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 10s
      timeout: 5s
      retries: 5
```

- [ ] **Step 2: Validate docker-compose config**

```bash
docker compose config --quiet && echo "OK"
```

Expected: `OK` (no YAML errors).

- [ ] **Step 3: Build the image**

```bash
docker compose build symbol-resolver
```

Expected: build completes without errors, image created.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(symbol-resolver): add to docker-compose"
```

---

## Task 9: Final verification

- [ ] **Step 1: Run all unit tests**

```bash
cd services/symbol-resolver && pytest tests/ -v
```

Expected: 14 tests, all PASS.

- [ ] **Step 2: Verify /health endpoint via Docker**

```bash
docker compose up -d redis neo4j symbol-resolver
sleep 15
curl http://localhost:8082/health
```

Expected: `{"status":"ok","service":"symbol-resolver","version":"0.1.0"}`

- [ ] **Step 3: Stop services**

```bash
docker compose down
```

- [ ] **Step 4: Final commit if any cleanup needed**

```bash
git add -p  # review any outstanding changes
git commit -m "chore(symbol-resolver): final cleanup"
```

---

## Definition of Done

- [ ] /health returns 200
- [ ] IMPORTS edges appear in Neo4j after processing cross-file imports
- [ ] External package imports create Package nodes
- [ ] Pending refs queue accumulates unresolved imports
- [ ] Pending refs resolve when the target file is later indexed
- [ ] All edge writes use MERGE (idempotent)
- [ ] All 14 unit tests pass
- [ ] Dockerfile builds cleanly
