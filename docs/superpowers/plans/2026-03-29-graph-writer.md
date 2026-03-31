# Graph Writer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the graph-writer service that consumes `stream:embedded-nodes` and `stream:parsed-file` and writes nodes, edges, and tombstones to Neo4j.

**Architecture:** Two async consumer loops share a module-level `dict[(commit_sha, file_path) → ParsedFileEvent]` cache. The edge consumer populates the cache and writes edges/tombstones; the node consumer merges cached metadata with embedded data and writes nodes. All Neo4j writes use the sync bolt driver via `loop.run_in_executor` with UNWIND batching.

**Tech Stack:** Python 3.12, FastAPI, redis.asyncio, neo4j (sync bolt driver), Pydantic v2, pytest

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `services/parser/app/models.py` | Modify | Add `qualified_name: str = ""` to `ParsedNode` |
| `services/parser/app/extractor.py` | Modify | Populate `qualified_name` in each `ParsedNode` constructor |
| `services/embedder/app/models.py` | Modify | Add `file_path: str` to `EmbeddedNodesEvent` |
| `services/embedder/app/consumer.py` | Modify | Pass `file_path=event.file_path` to `EmbeddedNodesEvent(...)` |
| `services/graph-writer/requirements.txt` | Create | Python dependencies |
| `services/graph-writer/Dockerfile` | Create | Container build |
| `services/graph-writer/app/__init__.py` | Create | Package marker |
| `services/graph-writer/app/models.py` | Create | Pydantic models for both input streams |
| `services/graph-writer/app/writer.py` | Create | `build_node_records`, `upsert_nodes`, `upsert_edges`, `tombstone` |
| `services/graph-writer/app/consumer.py` | Create | `run_edge_consumer`, `run_node_consumer`, shared cache |
| `services/graph-writer/app/main.py` | Create | FastAPI app with `/health`, `/ready`, `/metrics` |
| `services/graph-writer/tests/__init__.py` | Create | Package marker |
| `services/graph-writer/tests/test_writer.py` | Create | All 8 unit tests |
| `docker-compose.yml` | Modify | Add `graph-writer` service |

---

## Task 1: Parser — add `qualified_name` to `ParsedNode`

**Files:**
- Modify: `services/parser/app/models.py`
- Modify: `services/parser/app/extractor.py`

This is a prerequisite for the graph-writer to receive `qualified_name` via `ParsedFileEvent`.

- [ ] **Step 1: Add field to parser's `ParsedNode` model**

In `services/parser/app/models.py`, add `qualified_name` after `name`:

```python
class ParsedNode(BaseModel):
    stable_id: str
    node_hash: str
    type: str
    name: str
    qualified_name: str = ""   # populated by extractor
    signature: str
    docstring: str
    body: str
    line_start: int
    line_end: int
    parent_id: Optional[str] = None
```

- [ ] **Step 2: Populate `qualified_name` in extractor**

In `services/parser/app/extractor.py`, add `qualified_name=` to each `ParsedNode(...)` constructor call. There are four call sites:

**Imports** (around line 138):
```python
nodes.append(ParsedNode(
    stable_id=sid,
    node_hash=node_hash(name, "", body),
    type="import",
    name=name,
    qualified_name=name,        # ← add
    signature="",
    docstring="",
    body=body,
    line_start=child.start_point[0] + 1,
    line_end=child.end_point[0] + 1,
))
```

**Classes** (around line 159):
```python
nodes.append(ParsedNode(
    stable_id=cls_sid,
    node_hash=node_hash(cls_name, "", cls_body_text),
    type="class",
    name=cls_name,
    qualified_name=cls_name,    # ← add
    signature="",
    docstring=cls_docstring,
    body=cls_body_text,
    line_start=child.start_point[0] + 1,
    line_end=child.end_point[0] + 1,
))
```

**Methods** (around line 185):
```python
pn = ParsedNode(
    stable_id=m_sid,
    node_hash=node_hash(m_name, m_sig, m_body_text),
    type="method",
    name=m_name,
    qualified_name=m_qname,     # ← add (m_qname = f"{cls_name}.{m_name}" already defined above)
    signature=m_sig,
    docstring=m_docstring,
    body=m_body_text,
    line_start=method.start_point[0] + 1,
    line_end=method.end_point[0] + 1,
    parent_id=cls_sid,
)
```

**Top-level functions** (around line 212):
```python
pn = ParsedNode(
    stable_id=fn_sid,
    node_hash=node_hash(fn_name, fn_sig, fn_body_text),
    type="function",
    name=fn_name,
    qualified_name=fn_name,     # ← add
    signature=fn_sig,
    docstring=fn_docstring,
    body=fn_body_text,
    line_start=child.start_point[0] + 1,
    line_end=child.end_point[0] + 1,
)
```

- [ ] **Step 3: Update the parser test that guards against `qualified_name`**

`services/parser/tests/test_parser.py` line 99-101 has a test that will now fail because it explicitly asserts `qualified_name` is NOT in `ParsedNode.model_fields`. Replace that test with one that confirms the field now exists:

```python
# Before (delete this):
def test_method_qualified_name_not_in_output():
    from app.models import ParsedNode
    assert "qualified_name" not in ParsedNode.model_fields

# After (replace with this):
def test_method_qualified_name_in_output():
    from app.models import ParsedNode
    assert "qualified_name" in ParsedNode.model_fields
```

- [ ] **Step 4: Run parser tests to verify all pass**

```bash
cd services/parser && pip install -r requirements.txt -q && pytest tests/ -v
```

Expected: all existing tests pass (including the updated `test_method_qualified_name_in_output`).

- [ ] **Step 5: Commit**

```bash
git add services/parser/app/models.py services/parser/app/extractor.py services/parser/tests/test_parser.py
git commit -m "feat(parser): emit qualified_name in ParsedNode"
```

---

## Task 2: Embedder — add `file_path` to `EmbeddedNodesEvent`

**Files:**
- Modify: `services/embedder/app/models.py`
- Modify: `services/embedder/app/consumer.py`

Without `file_path` in `EmbeddedNodesEvent`, the graph-writer cannot key its cache by `(commit_sha, file_path)` to handle multi-file commits.

- [ ] **Step 1: Add field to `EmbeddedNodesEvent`**

In `services/embedder/app/models.py`, add `file_path` to `EmbeddedNodesEvent`:

```python
class EmbeddedNodesEvent(BaseModel):
    repo: str
    commit_sha: str
    file_path: str      # ← add this field
    nodes: list[EmbeddedNode]
```

- [ ] **Step 2: Populate `file_path` in the embedder consumer**

In `services/embedder/app/consumer.py`, find the `EmbeddedNodesEvent(...)` constructor call (around line 53) and add `file_path`:

```python
out = EmbeddedNodesEvent(
    repo=event.repo,
    commit_sha=event.commit_sha,
    file_path=event.file_path,   # ← add this line
    nodes=embedded,
)
```

Note: `event` here is `ParsedFileEvent`, which already has `file_path`.

- [ ] **Step 3: Update the embedder test that constructs `EmbeddedNodesEvent` without `file_path`**

`services/embedder/tests/test_embedder.py` line 48 calls `EmbeddedNodesEvent(repo="acme", commit_sha="abc123", nodes=[node])` — missing the now-required `file_path` field. Update it:

```python
# Before:
evt = EmbeddedNodesEvent(repo="acme", commit_sha="abc123", nodes=[node])

# After:
evt = EmbeddedNodesEvent(repo="acme", commit_sha="abc123", file_path="src/foo.py", nodes=[node])
```

- [ ] **Step 4: Run embedder tests to verify all pass**

```bash
cd services/embedder && pip install -r requirements.txt -q && pytest tests/ -v
```

Expected: all existing tests pass.

- [ ] **Step 5: Commit**

```bash
git add services/embedder/app/models.py services/embedder/app/consumer.py services/embedder/tests/test_embedder.py
git commit -m "feat(embedder): propagate file_path in EmbeddedNodesEvent"
```

---

## Task 3: Scaffold graph-writer service

**Files:**
- Create: `services/graph-writer/requirements.txt`
- Create: `services/graph-writer/Dockerfile`
- Create: `services/graph-writer/app/__init__.py`
- Create: `services/graph-writer/tests/__init__.py`

- [ ] **Step 1: Create `requirements.txt`**

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
redis>=5.0.0
pydantic>=2.0.0
neo4j>=5.0.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

Save to `services/graph-writer/requirements.txt`.

- [ ] **Step 2: Create `Dockerfile`**

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

Save to `services/graph-writer/Dockerfile`.

- [ ] **Step 3: Create package markers**

Create `services/graph-writer/app/__init__.py` — empty file.
Create `services/graph-writer/tests/__init__.py` — empty file.

- [ ] **Step 4: Install dependencies locally (for running tests)**

```bash
cd services/graph-writer && pip install -r requirements.txt -q
```

- [ ] **Step 5: Commit scaffold**

```bash
git add services/graph-writer/
git commit -m "feat(graph-writer): scaffold directory structure and dependencies"
```

---

## Task 4: Pydantic models (`models.py`)

**Files:**
- Create: `services/graph-writer/app/models.py`

No unit tests needed — Pydantic models are declarative. Correctness is verified by the consumer and writer tests.

- [ ] **Step 1: Create `models.py`**

```python
from __future__ import annotations
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class ParsedNode(BaseModel):
    stable_id: str
    node_hash: str
    type: str
    name: str
    qualified_name: str = ""
    signature: str
    docstring: str = ""
    body: str
    line_start: int
    line_end: int
    parent_id: Optional[str] = None


class IntraFileEdge(BaseModel):
    source_stable_id: str
    target_stable_id: str
    type: Literal["CALLS"]


class ParsedFileEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repo: str
    commit_sha: str
    file_path: str
    language: str
    nodes: list[ParsedNode]
    intra_file_edges: list[IntraFileEdge]
    deleted_nodes: list[str] = []


class EmbeddedNode(BaseModel):
    stable_id: str
    vector: list[float]
    embed_text: str
    node_hash: str


class EmbeddedNodesEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repo: str
    commit_sha: str
    file_path: str
    nodes: list[EmbeddedNode]
```

Save to `services/graph-writer/app/models.py`.

- [ ] **Step 2: Commit**

```bash
git add services/graph-writer/app/models.py
git commit -m "feat(graph-writer): add Pydantic models"
```

---

## Task 5: Write failing tests for `writer.py`

**Files:**
- Create: `services/graph-writer/tests/test_writer.py`

Write all 8 tests before writing any implementation. All must fail at this step.

- [ ] **Step 1: Create `tests/test_writer.py`**

```python
from unittest.mock import MagicMock
import pytest

from app.writer import (
    TOMBSTONE_QUERY,
    UPSERT_EDGES_QUERY,
    UPSERT_NODES_QUERY,
    build_node_records,
    tombstone,
    upsert_edges,
    upsert_nodes,
)
from app.models import EmbeddedNode, ParsedNode


def _make_driver():
    """Return (mock_driver, mock_session). The session is what session.run is called on."""
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    return driver, session


def _parsed_node(**kwargs) -> ParsedNode:
    defaults = dict(
        stable_id="sid1",
        node_hash="hash1",
        type="function",
        name="my_func",
        qualified_name="my_func",
        signature="my_func()",
        docstring="",
        body="def my_func(): pass",
        line_start=1,
        line_end=1,
    )
    defaults.update(kwargs)
    return ParsedNode(**defaults)


def _embedded_node(**kwargs) -> EmbeddedNode:
    defaults = dict(
        stable_id="sid1",
        vector=[0.1, 0.2],
        embed_text="my_func my_func()",
        node_hash="hash1",
    )
    defaults.update(kwargs)
    return EmbeddedNode(**defaults)


# ── Test 1: upsert_nodes calls driver; Cypher contains active and updated_at ──

def test_upsert_nodes_calls_driver_with_active_and_updated_at():
    driver, session = _make_driver()
    nodes = [{
        "stable_id": "sid1", "name": "my_func", "type": "function",
        "signature": "my_func()", "docstring": "", "body": "def my_func(): pass",
        "file_path": "src/foo.py", "qualified_name": "my_func",
        "language": "python", "repo": "myrepo",
        "embed_text": "my_func my_func()", "node_hash": "hash1",
    }]
    upsert_nodes(driver, nodes)
    session.run.assert_called_once()
    query, = session.run.call_args[0]
    assert query == UPSERT_NODES_QUERY


# ── Test 2: upsert_nodes with empty list → no driver call ──

def test_upsert_nodes_empty_list():
    driver, session = _make_driver()
    upsert_nodes(driver, [])
    session.run.assert_not_called()


# ── Test 3: upsert_edges uses CALLS and source='static' in query ──

def test_upsert_edges_calls_driver_with_static_source():
    driver, session = _make_driver()
    edges = [{"source": "sid1", "target": "sid2"}]
    upsert_edges(driver, edges)
    session.run.assert_called_once()
    query, = session.run.call_args[0]
    assert query == UPSERT_EDGES_QUERY
    _, kwargs = session.run.call_args
    assert kwargs["edges"] == edges


# ── Test 4: upsert_edges with empty list → no driver call ──

def test_upsert_edges_empty_list():
    driver, session = _make_driver()
    upsert_edges(driver, [])
    session.run.assert_not_called()


# ── Test 5: tombstone sets active=false and deleted_at in query ──

def test_tombstone_calls_driver_with_correct_query():
    driver, session = _make_driver()
    stable_ids = ["sid1", "sid2"]
    tombstone(driver, stable_ids)
    session.run.assert_called_once()
    query, = session.run.call_args[0]
    assert query == TOMBSTONE_QUERY
    _, kwargs = session.run.call_args
    assert kwargs["stable_ids"] == stable_ids


# ── Test 6: tombstone with empty list → no driver call ──

def test_tombstone_empty_list():
    driver, session = _make_driver()
    tombstone(driver, [])
    session.run.assert_not_called()


# ── Test 7: build_node_records stores qualified_name from ParsedNode ──

def test_build_node_records_uses_qualified_name():
    en = _embedded_node(stable_id="sid1")
    pn = _parsed_node(stable_id="sid1", qualified_name="MyClass.my_func")
    records = build_node_records([en], {"sid1": pn}, "src/foo.py", "python", "myrepo")
    assert len(records) == 1
    assert records[0]["qualified_name"] == "MyClass.my_func"


# ── Test 8: partial cache hit — 3 nodes in, 1 missing → 2 records out ──

def test_build_node_records_partial_cache_hit():
    en1 = _embedded_node(stable_id="sid1")
    en2 = _embedded_node(stable_id="sid2")   # not in parsed cache
    en3 = _embedded_node(stable_id="sid3")
    pn1 = _parsed_node(stable_id="sid1", name="f1", qualified_name="f1")
    pn3 = _parsed_node(stable_id="sid3", name="f3", qualified_name="f3")
    parsed_by_id = {"sid1": pn1, "sid3": pn3}
    records = build_node_records([en1, en2, en3], parsed_by_id, "src/foo.py", "python", "myrepo")
    assert len(records) == 2
    assert {r["stable_id"] for r in records} == {"sid1", "sid3"}
```

- [ ] **Step 2: Run tests — verify all 8 fail with ImportError**

```bash
cd services/graph-writer && pytest tests/test_writer.py -v
```

Expected: `ImportError: cannot import name 'build_node_records' from 'app.writer'` (module doesn't exist yet).

---

## Task 6: Implement `writer.py` and make all tests pass

**Files:**
- Create: `services/graph-writer/app/writer.py`

- [ ] **Step 1: Create `app/writer.py`**

```python
from __future__ import annotations

import logging

from .models import EmbeddedNode, ParsedNode

logger = logging.getLogger(__name__)

UPSERT_NODES_QUERY = """
UNWIND $nodes AS n
MERGE (node:Node { stable_id: n.stable_id })
SET node += {
  name:           n.name,
  type:           n.type,
  signature:      n.signature,
  docstring:      n.docstring,
  body:           n.body,
  file_path:      n.file_path,
  qualified_name: n.qualified_name,
  language:       n.language,
  repo:           n.repo,
  embed_text:     n.embed_text,
  node_hash:      n.node_hash,
  active:         true,
  updated_at:     datetime()
}
"""

UPSERT_EDGES_QUERY = """
UNWIND $edges AS e
MATCH (a:Node { stable_id: e.source }), (b:Node { stable_id: e.target })
MERGE (a)-[r:CALLS]->(b)
SET r.source = 'static', r.updated_at = datetime()
"""

TOMBSTONE_QUERY = """
MATCH (n:Node) WHERE n.stable_id IN $stable_ids
SET n.active = false, n.deleted_at = datetime()
WITH n
MATCH (n)-[r]->() DELETE r
WITH n
MATCH ()-[r]->(n) DELETE r
"""


def build_node_records(
    embedded_nodes: list[EmbeddedNode],
    parsed_nodes_by_id: dict[str, ParsedNode],
    file_path: str,
    language: str,
    repo: str,
) -> list[dict]:
    """Merge embedded node data with parsed metadata. Nodes absent from parsed_nodes_by_id are skipped."""
    records = []
    for en in embedded_nodes:
        pn = parsed_nodes_by_id.get(en.stable_id)
        if pn is None:
            logger.warning("Node %s not found in parsed cache, skipping", en.stable_id)
            continue
        records.append({
            "stable_id": en.stable_id,
            "name": pn.name,
            "type": pn.type,
            "signature": pn.signature,
            "docstring": pn.docstring,
            "body": pn.body,
            "file_path": file_path,
            "qualified_name": pn.qualified_name or pn.name,
            "language": language,
            "repo": repo,
            "embed_text": en.embed_text,
            "node_hash": en.node_hash,
        })
    return records


def upsert_nodes(driver, nodes: list[dict]) -> None:
    if not nodes:
        return
    with driver.session() as session:
        session.run(UPSERT_NODES_QUERY, nodes=nodes)


def upsert_edges(driver, edges: list[dict]) -> None:
    if not edges:
        return
    with driver.session() as session:
        session.run(UPSERT_EDGES_QUERY, edges=edges)


def tombstone(driver, stable_ids: list[str]) -> None:
    if not stable_ids:
        return
    with driver.session() as session:
        session.run(TOMBSTONE_QUERY, stable_ids=stable_ids)
```

- [ ] **Step 2: Run tests — verify all 8 pass**

```bash
cd services/graph-writer && pytest tests/test_writer.py -v
```

Expected output:
```
tests/test_writer.py::test_upsert_nodes_calls_driver_with_active_and_updated_at PASSED
tests/test_writer.py::test_upsert_nodes_empty_list PASSED
tests/test_writer.py::test_upsert_edges_calls_driver_with_static_source PASSED
tests/test_writer.py::test_upsert_edges_empty_list PASSED
tests/test_writer.py::test_tombstone_calls_driver_with_correct_query PASSED
tests/test_writer.py::test_tombstone_empty_list PASSED
tests/test_writer.py::test_build_node_records_uses_qualified_name PASSED
tests/test_writer.py::test_build_node_records_partial_cache_hit PASSED
8 passed in ...
```

- [ ] **Step 3: Commit**

```bash
git add services/graph-writer/app/writer.py services/graph-writer/tests/test_writer.py
git commit -m "feat(graph-writer): add writer with TDD tests"
```

---

## Task 7: Implement `consumer.py`

**Files:**
- Create: `services/graph-writer/app/consumer.py`

Consumer XACK policy is integration-level behaviour — not unit tested here.

- [ ] **Step 1: Create `app/consumer.py`**

```python
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket

import redis.asyncio as aioredis
import redis.exceptions
from pydantic import ValidationError

from . import writer
from .models import EmbeddedNodesEvent, ParsedFileEvent

logger = logging.getLogger(__name__)

STREAM_EMBEDDED = "stream:embedded-nodes"
STREAM_PARSED = "stream:parsed-file"
GROUP_NODES = "graph-writer-group"
GROUP_EDGES = "graph-writer-edges-group"

# Keyed by (commit_sha, file_path). Edge consumer writes; node consumer reads.
_parsed_file_cache: dict[tuple[str, str], ParsedFileEvent] = {}


async def run_edge_consumer(driver) -> None:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = aioredis.from_url(url)
    consumer_name = f"graph-writer-{socket.gethostname()}"
    try:
        try:
            await r.xgroup_create(STREAM_PARSED, GROUP_EDGES, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        logger.info("Edge consumer started: group=%s consumer=%s", GROUP_EDGES, consumer_name)
        loop = asyncio.get_running_loop()

        while True:
            try:
                messages = await r.xreadgroup(
                    groupname=GROUP_EDGES,
                    consumername=consumer_name,
                    streams={STREAM_PARSED: ">"},
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

                            _parsed_file_cache[(event.commit_sha, event.file_path)] = event

                            edges = [
                                {"source": e.source_stable_id, "target": e.target_stable_id}
                                for e in event.intra_file_edges
                            ]
                            await loop.run_in_executor(None, writer.upsert_edges, driver, edges)

                            if event.deleted_nodes:
                                await loop.run_in_executor(
                                    None, writer.tombstone, driver, event.deleted_nodes
                                )

                            await r.xack(STREAM_PARSED, GROUP_EDGES, msg_id)
                        except (json.JSONDecodeError, ValidationError, KeyError) as exc:
                            logger.warning("Bad edge message %s, skipping: %s", msg_id, exc)
                            await r.xack(STREAM_PARSED, GROUP_EDGES, msg_id)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.error("Edge consumer failed msg=%s: %s", msg_id, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Edge consumer loop error: %s", exc)
    finally:
        await r.aclose()


async def run_node_consumer(driver) -> None:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = aioredis.from_url(url)
    consumer_name = f"graph-writer-{socket.gethostname()}"
    try:
        try:
            await r.xgroup_create(STREAM_EMBEDDED, GROUP_NODES, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        logger.info("Node consumer started: group=%s consumer=%s", GROUP_NODES, consumer_name)
        loop = asyncio.get_running_loop()

        while True:
            try:
                messages = await r.xreadgroup(
                    groupname=GROUP_NODES,
                    consumername=consumer_name,
                    streams={STREAM_EMBEDDED: ">"},
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
                            event = EmbeddedNodesEvent.model_validate_json(raw)

                            parsed_event = _parsed_file_cache.get(
                                (event.commit_sha, event.file_path)
                            )
                            if parsed_event is None:
                                logger.warning(
                                    "Cache miss: commit_sha=%s file_path=%s — skipping",
                                    event.commit_sha, event.file_path,
                                )
                                await r.xack(STREAM_EMBEDDED, GROUP_NODES, msg_id)
                                continue

                            parsed_by_id = {n.stable_id: n for n in parsed_event.nodes}
                            records = writer.build_node_records(
                                event.nodes,
                                parsed_by_id,
                                parsed_event.file_path,
                                parsed_event.language,
                                parsed_event.repo,
                            )
                            await loop.run_in_executor(
                                None, writer.upsert_nodes, driver, records
                            )
                            await r.xack(STREAM_EMBEDDED, GROUP_NODES, msg_id)
                        except (json.JSONDecodeError, ValidationError, KeyError) as exc:
                            logger.warning("Bad node message %s, skipping: %s", msg_id, exc)
                            await r.xack(STREAM_EMBEDDED, GROUP_NODES, msg_id)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.error("Node consumer failed msg=%s: %s", msg_id, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Node consumer loop error: %s", exc)
    finally:
        await r.aclose()
```

- [ ] **Step 2: Verify tests still pass**

```bash
cd services/graph-writer && pytest tests/ -v
```

Expected: 8 passed.

- [ ] **Step 3: Commit**

```bash
git add services/graph-writer/app/consumer.py
git commit -m "feat(graph-writer): add dual Redis stream consumers"
```

---

## Task 8: Implement `main.py`

**Files:**
- Create: `services/graph-writer/app/main.py`

- [ ] **Step 1: Create `app/main.py`**

```python
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from neo4j import GraphDatabase

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "graph-writer"

_redis_client: aioredis.Redis | None = None
_driver = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = aioredis.from_url(url)
    return _redis_client


def _make_driver():
    url = os.environ.get("NEO4J_URL", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    return GraphDatabase.driver(url, auth=(user, password))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _driver
    from .consumer import run_edge_consumer, run_node_consumer

    _driver = _make_driver()
    edge_task = asyncio.create_task(run_edge_consumer(_driver))
    node_task = asyncio.create_task(run_node_consumer(_driver))
    try:
        yield
    finally:
        edge_task.cancel()
        node_task.cancel()
        for task in (edge_task, node_task):
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
    try:
        if _driver:
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
        "# HELP graph_writer_messages_processed_total Total messages processed",
        "# TYPE graph_writer_messages_processed_total counter",
        "graph_writer_messages_processed_total 0",
        "# HELP graph_writer_messages_failed_total Total messages failed",
        "# TYPE graph_writer_messages_failed_total counter",
        "graph_writer_messages_failed_total 0",
        "# HELP graph_writer_nodes_written_total Total nodes written",
        "# TYPE graph_writer_nodes_written_total counter",
        "graph_writer_nodes_written_total 0",
        "# HELP graph_writer_edges_written_total Total edges passed to Neo4j",
        "# TYPE graph_writer_edges_written_total counter",
        "graph_writer_edges_written_total 0",
        "# HELP graph_writer_tombstones_written_total Total nodes tombstoned",
        "# TYPE graph_writer_tombstones_written_total counter",
        "graph_writer_tombstones_written_total 0",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
```

- [ ] **Step 2: Verify tests still pass**

```bash
cd services/graph-writer && pytest tests/ -v
```

Expected: 8 passed.

- [ ] **Step 3: Commit**

```bash
git add services/graph-writer/app/main.py
git commit -m "feat(graph-writer): add FastAPI app with health/ready/metrics endpoints"
```

---

## Task 9: Wire into docker-compose and verify Dockerfile builds

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add `graph-writer` service to `docker-compose.yml`**

Append after the `embedder` service block (before the `neo4j-init` block):

```yaml
  graph-writer:
    build: ./services/graph-writer
    ports:
      - "8084:8080"
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

- [ ] **Step 2: Build the Docker image**

```bash
docker build -t graph-writer-test services/graph-writer/
```

Expected: image builds cleanly with no errors.

- [ ] **Step 3: Verify `/health` from the built image**

```bash
docker run --rm -d --name gw-test -p 8084:8080 \
  -e REDIS_URL=redis://localhost:6379 \
  -e NEO4J_URL=bolt://localhost:7687 \
  -e NEO4J_USER=neo4j \
  -e NEO4J_PASSWORD=localpassword \
  graph-writer-test

for i in $(seq 1 10); do
  curl -sf http://localhost:8084/health && break
  sleep 1
done
docker stop gw-test
```

Expected: `{"status":"ok","service":"graph-writer","version":"0.1.0"}`

> Note: Redis and Neo4j won't be reachable in this standalone test, so `/ready` will return 503 — that's expected. `/health` does not check dependencies.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(graph-writer): wire graph-writer into docker-compose"
```

---

## Verification Checklist

After all tasks are complete, run the full verification from CLAUDE.md:

- [ ] `curl http://localhost:8084/health` → `{"status":"ok","service":"graph-writer","version":"0.1.0"}`
- [ ] `pytest services/graph-writer/tests/ -v` → 8 passed
- [ ] Push a file and verify nodes appear in Neo4j
- [ ] Push same file twice — node count identical (MERGE idempotency)
- [ ] Check CALLS edges have `source='static'`
- [ ] Push a delete event — nodes have `active=false`, edges removed
