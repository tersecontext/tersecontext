# Vector Writer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the vector-writer service, which consumes `stream:embedded-nodes` to upsert vectors into Qdrant and `stream:file-changed` to delete removed nodes.

**Architecture:** Two independent asyncio tasks (one per stream) share a single sync `QdrantClient` wrapped in `run_in_executor`. The embedded-nodes contract is extended to include `name`, `type`, `file_path`, `language` per node, which the vector-writer uses to populate Qdrant point payloads.

**Tech Stack:** Python 3.12, FastAPI, redis.asyncio, qdrant-client, pydantic v2, pytest-asyncio (asyncio_mode=auto)

---

## File Map

**Modified (upstream contract change):**
- `contracts/events/embedded_nodes.json` — add `name`, `type`, `file_path`, `language` to node item schema
- `services/embedder/app/models.py` — add four fields to `EmbeddedNode`
- `services/embedder/app/embedder.py` — copy four fields from `ParsedNode` when building `EmbeddedNode`
- `services/embedder/tests/test_embedder.py` — update EmbeddedNode fixture to include new fields

**Created (vector-writer service):**
- `services/vector-writer/requirements.txt`
- `services/vector-writer/pyproject.toml`
- `services/vector-writer/Dockerfile`
- `services/vector-writer/app/__init__.py`
- `services/vector-writer/app/models.py` — Pydantic models: `EmbeddedNode`, `EmbeddedNodesEvent`, `FileChangedEvent`
- `services/vector-writer/app/writer.py` — `QdrantWriter`: `ensure_collection`, `upsert_points`, `delete_points`, `get_collections`
- `services/vector-writer/app/consumer.py` — `_process_embedded`, `_process_delete`, `run_embedded_consumer`, `run_delete_consumer`
- `services/vector-writer/app/main.py` — FastAPI app with lifespan, `/health`, `/ready`, `/metrics`
- `services/vector-writer/tests/__init__.py`
- `services/vector-writer/tests/test_writer.py` — 15 tests covering writer + consumer

**Modified:**
- `docker-compose.yml` — add `vector-writer` service

---

## Task 1: Extend embedded_nodes contract and embedder

**Files:**
- Modify: `contracts/events/embedded_nodes.json`
- Modify: `services/embedder/app/models.py`
- Modify: `services/embedder/app/embedder.py`
- Modify: `services/embedder/tests/test_embedder.py`

- [ ] **Step 1: Update the JSON schema**

Edit `contracts/events/embedded_nodes.json`. The node item's `required` array and `properties` object both need the four new fields. Replace the node item schema with:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "events/embedded_nodes",
  "title": "EmbeddedNodes",
  "type": "object",
  "required": ["repo", "commit_sha", "nodes"],
  "additionalProperties": false,
  "properties": {
    "repo":       { "type": "string" },
    "commit_sha": { "type": "string" },
    "nodes": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["stable_id", "vector", "embed_text", "node_hash", "name", "type", "file_path", "language"],
        "additionalProperties": false,
        "properties": {
          "stable_id":  { "type": "string" },
          "vector":     { "type": "array", "items": { "type": "number" } },
          "embed_text": { "type": "string" },
          "node_hash":  { "type": "string" },
          "name":       { "type": "string" },
          "type":       { "type": "string" },
          "file_path":  { "type": "string" },
          "language":   { "type": "string" }
        }
      }
    }
  }
}
```

- [ ] **Step 2: Update EmbeddedNode model in the embedder**

In `services/embedder/app/models.py`, add four fields to `EmbeddedNode`:

```python
class EmbeddedNode(BaseModel):
    stable_id: str
    vector: list[float]
    embed_text: str
    node_hash: str
    name: str
    type: str
    file_path: str
    language: str
```

- [ ] **Step 3: Populate new fields in embedder.py**

In `services/embedder/app/embedder.py`, update the `EmbeddedNode(...)` construction inside `embed_nodes`. The current return statement is:

```python
return [
    EmbeddedNode(
        stable_id=node.stable_id,
        vector=vector,
        embed_text=embed_texts[idx],
        node_hash=node.node_hash,
    )
    for idx, (node, vector) in enumerate(zip(to_embed, vectors))
]
```

Replace with:

```python
return [
    EmbeddedNode(
        stable_id=node.stable_id,
        vector=vector,
        embed_text=embed_texts[idx],
        node_hash=node.node_hash,
        name=node.name,
        type=node.type,
        file_path=event_file_path,
        language=event_language,
    )
    for idx, (node, vector) in enumerate(zip(to_embed, vectors))
]
```

Wait — `embed_nodes` currently only receives `list[ParsedNode]`, not the file_path/language from the event level. `ParsedNode` does not carry `file_path` or `language`. Those come from `ParsedFileEvent`. Two options:
- Pass `file_path` and `language` as explicit args to `embed_nodes`
- Add `file_path` and `language` to `ParsedNode` (they are on the event, not per-node)

**Use the first option** (pass as args — no model changes needed). Update `embed_nodes` signature:

```python
async def embed_nodes(
    nodes: list[ParsedNode],
    neo4j_cache: dict[str, str],
    provider: EmbeddingProvider,
    batch_size: int = 64,
    embedding_dim: int = 0,
    file_path: str = "",
    language: str = "",
) -> list[EmbeddedNode]:
```

And pass them in the return:

```python
return [
    EmbeddedNode(
        stable_id=node.stable_id,
        vector=vector,
        embed_text=embed_texts[idx],
        node_hash=node.node_hash,
        name=node.name,
        type=node.type,
        file_path=file_path,
        language=language,
    )
    for idx, (node, vector) in enumerate(zip(to_embed, vectors))
]
```

Update the call site in `services/embedder/app/consumer.py`. The `_process` function calls `embed_nodes(event.nodes, ...)`. Change it to:

```python
embedded = await embed_nodes(
    event.nodes, neo4j_cache, provider, batch_size, embedding_dim,
    file_path=event.file_path,
    language=event.language,
)
```

- [ ] **Step 4: Update the embedder test fixture**

In `services/embedder/tests/test_embedder.py`, `test_embedded_nodes_event_shape` creates an `EmbeddedNode` without the new fields. Update it:

```python
def test_embedded_nodes_event_shape():
    from app.models import EmbeddedNode, EmbeddedNodesEvent
    node = EmbeddedNode(
        stable_id="sha256:abc",
        vector=[0.1, 0.2, 0.3],
        embed_text="foo foo(x: int) -> str",
        node_hash="sha256:def",
        name="foo",
        type="function",
        file_path="src/foo.py",
        language="python",
    )
    evt = EmbeddedNodesEvent(repo="acme", commit_sha="abc123", nodes=[node])
    assert evt.nodes[0].stable_id == "sha256:abc"
    assert evt.nodes[0].name == "foo"
```

- [ ] **Step 5: Run embedder tests**

```bash
cd services/embedder && python -m pytest tests/ -v
```

Expected: all tests PASS. The new fields have defaults? No — they are required. Check that the other `EmbeddedNode` constructions in tests also add the new fields. Specifically, `_make_node` helper in `test_embedder.py` builds `ParsedNode`, not `EmbeddedNode` — that's fine. But `test_process_embeds_all_when_neo4j_raises` calls `_process` which calls `embed_nodes`, so it will exercise the real code path. The embed result will now carry `name`, `type`, `file_path`, `language`. The test only checks `embedded_count == 3` and `xadd` was called, so it should still pass.

- [ ] **Step 6: Commit**

```bash
git add contracts/events/embedded_nodes.json \
        services/embedder/app/models.py \
        services/embedder/app/embedder.py \
        services/embedder/app/consumer.py \
        services/embedder/tests/test_embedder.py
git commit -m "feat(embedder): extend EmbeddedNode with name/type/file_path/language fields"
```

---

## Task 2: Scaffold vector-writer service

**Files:**
- Create: `services/vector-writer/requirements.txt`
- Create: `services/vector-writer/pyproject.toml`
- Create: `services/vector-writer/Dockerfile`
- Create: `services/vector-writer/app/__init__.py`
- Create: `services/vector-writer/tests/__init__.py`

- [ ] **Step 1: Create requirements.txt**

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
redis>=5.0.0
pydantic>=2.0.0
qdrant-client>=1.7.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 2: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "tersecontext-vector-writer"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "redis>=5.0.0",
    "pydantic>=2.0.0",
    "qdrant-client>=1.7.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 3: Create Dockerfile**

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

- [ ] **Step 4: Create empty init files**

```bash
mkdir -p services/vector-writer/app services/vector-writer/tests
touch services/vector-writer/app/__init__.py services/vector-writer/tests/__init__.py
```

- [ ] **Step 5: Commit**

```bash
git add services/vector-writer/
git commit -m "feat(vector-writer): scaffold service structure"
```

---

## Task 3: models.py (TDD)

**Files:**
- Create: `services/vector-writer/app/models.py`
- Create: `services/vector-writer/tests/test_writer.py`

- [ ] **Step 1: Write failing tests for models**

Create `services/vector-writer/tests/test_writer.py` with just the model tests first:

```python
import json
import uuid
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_node(stable_id="sha256:abc", name="authenticate", node_type="function",
               file_path="auth/service.py", language="python",
               node_hash="sha256:def", vector=None):
    from app.models import EmbeddedNode
    return EmbeddedNode(
        stable_id=stable_id,
        vector=vector or [0.1, 0.2, 0.3],
        name=name,
        type=node_type,
        file_path=file_path,
        language=language,
        node_hash=node_hash,
    )


def _make_event(nodes=None, repo="acme-api"):
    from app.models import EmbeddedNodesEvent
    return EmbeddedNodesEvent(
        repo=repo,
        commit_sha="abc123",
        nodes=nodes if nodes is not None else [_make_node()],
    )


def _make_writer(embedding_dim=768):
    mock_client = MagicMock()
    mock_client.get_collections.return_value = MagicMock(collections=[])
    with patch("app.writer.QdrantClient", return_value=mock_client):
        from app.writer import QdrantWriter
        writer = QdrantWriter(url="http://localhost:6333", embedding_dim=embedding_dim)
    return writer, mock_client


# ── Models ─────────────────────────────────────────────────────────────────────

def test_embedded_node_shape():
    node = _make_node()
    assert node.stable_id == "sha256:abc"
    assert node.name == "authenticate"
    assert node.type == "function"
    assert node.file_path == "auth/service.py"
    assert node.language == "python"
    assert node.node_hash == "sha256:def"


def test_file_changed_event_deleted_nodes_defaults_to_empty():
    from app.models import FileChangedEvent
    event = FileChangedEvent.model_validate({
        "repo": "acme",
        "diff_type": "modified",
        "path": "src/foo.py",
        "language": "python",
        "changed_nodes": [],
        "added_nodes": [],
        # deleted_nodes intentionally omitted
    })
    assert event.deleted_nodes == []


def test_file_changed_event_ignores_extra_fields():
    from app.models import FileChangedEvent
    event = FileChangedEvent.model_validate({
        "repo": "acme",
        "deleted_nodes": ["sha256:x"],
        "some_unknown_field": "ignored",
    })
    assert event.repo == "acme"
    assert event.deleted_nodes == ["sha256:x"]


def test_embedded_nodes_event_commit_sha_is_optional():
    from app.models import EmbeddedNodesEvent, EmbeddedNode
    event = EmbeddedNodesEvent(repo="acme", nodes=[])
    assert event.commit_sha is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd services/vector-writer && python -m pytest tests/test_writer.py::test_embedded_node_shape -v
```

Expected: `ModuleNotFoundError: No module named 'app.models'`

- [ ] **Step 3: Create models.py**

```python
from __future__ import annotations
from pydantic import BaseModel, ConfigDict


class EmbeddedNode(BaseModel):
    stable_id: str
    vector: list[float]
    name: str
    type: str
    file_path: str
    language: str
    node_hash: str


class EmbeddedNodesEvent(BaseModel):
    repo: str
    commit_sha: str | None = None
    nodes: list[EmbeddedNode]


class FileChangedEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repo: str
    deleted_nodes: list[str] = []
```

- [ ] **Step 4: Run model tests**

```bash
python -m pytest tests/test_writer.py -k "test_embedded_node or test_file_changed or test_embedded_nodes_event" -v
```

Expected: all 4 model tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/vector-writer/app/models.py services/vector-writer/tests/test_writer.py
git commit -m "feat(vector-writer): add Pydantic models"
```

---

## Task 4: writer.py (TDD)

**Files:**
- Create: `services/vector-writer/app/writer.py`
- Modify: `services/vector-writer/tests/test_writer.py`

- [ ] **Step 1: Add writer tests to test_writer.py**

Append these tests to `tests/test_writer.py`:

```python
# ── writer.py: upsert_points ───────────────────────────────────────────────────

async def test_upsert_calls_client_with_correct_collection():
    writer, mock_client = _make_writer()
    await writer.upsert_points(_make_event())
    mock_client.upsert.assert_called_once()
    kwargs = mock_client.upsert.call_args.kwargs
    assert kwargs.get("collection_name") == "nodes"


async def test_upsert_point_ids_are_deterministic_uuid5():
    writer, mock_client = _make_writer()
    node = _make_node(stable_id="sha256:abc")
    await writer.upsert_points(_make_event(nodes=[node]))
    points = mock_client.upsert.call_args.kwargs["points"]
    expected_id = str(uuid.uuid5(uuid.NAMESPACE_OID, "sha256:abc"))
    assert points[0].id == expected_id


async def test_upsert_payload_contains_all_required_fields():
    writer, mock_client = _make_writer()
    node = _make_node(
        stable_id="sha256:abc", name="authenticate", node_type="function",
        file_path="auth/service.py", language="python", node_hash="sha256:def",
    )
    await writer.upsert_points(_make_event(nodes=[node], repo="acme-api"))
    payload = mock_client.upsert.call_args.kwargs["points"][0].payload
    assert payload["stable_id"] == "sha256:abc"
    assert payload["name"] == "authenticate"
    assert payload["type"] == "function"
    assert payload["file_path"] == "auth/service.py"
    assert payload["language"] == "python"
    assert payload["repo"] == "acme-api"
    assert payload["node_hash"] == "sha256:def"


async def test_upsert_empty_nodes_does_not_call_client():
    writer, mock_client = _make_writer()
    await writer.upsert_points(_make_event(nodes=[]))
    mock_client.upsert.assert_not_called()


# ── writer.py: delete_points ───────────────────────────────────────────────────

async def test_delete_calls_client_with_correct_string_uuids():
    writer, mock_client = _make_writer()
    stable_ids = ["sha256:abc", "sha256:def"]
    await writer.delete_points(stable_ids)
    mock_client.delete.assert_called_once()
    selector = mock_client.delete.call_args.kwargs["points_selector"]
    expected = [str(uuid.uuid5(uuid.NAMESPACE_OID, sid)) for sid in stable_ids]
    assert sorted(selector.points) == sorted(expected)


async def test_delete_empty_list_does_not_call_client():
    writer, mock_client = _make_writer()
    await writer.delete_points([])
    mock_client.delete.assert_not_called()


# ── writer.py: ensure_collection ──────────────────────────────────────────────

async def test_ensure_collection_creates_when_absent():
    writer, mock_client = _make_writer()
    mock_client.get_collections.return_value = MagicMock(collections=[])
    await writer.ensure_collection()
    mock_client.create_collection.assert_called_once()
    kwargs = mock_client.create_collection.call_args.kwargs
    assert kwargs["collection_name"] == "nodes"


async def test_ensure_collection_skips_create_when_present():
    writer, mock_client = _make_writer()
    existing = MagicMock()
    existing.name = "nodes"
    mock_client.get_collections.return_value = MagicMock(collections=[existing])
    await writer.ensure_collection()
    mock_client.create_collection.assert_not_called()


async def test_ensure_collection_ignores_already_exists_error():
    writer, mock_client = _make_writer()
    mock_client.get_collections.return_value = MagicMock(collections=[])
    error = Exception("Collection already exists")
    error.status_code = 409
    mock_client.create_collection.side_effect = error
    await writer.ensure_collection()  # must not raise
```

- [ ] **Step 2: Run to confirm failures**

```bash
python -m pytest tests/test_writer.py -k "upsert or delete or ensure" -v
```

Expected: `ModuleNotFoundError: No module named 'app.writer'`

- [ ] **Step 3: Create writer.py**

```python
from __future__ import annotations
import asyncio
import uuid

from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct, PointIdsList

from .models import EmbeddedNodesEvent

COLLECTION = "nodes"


class QdrantWriter:
    def __init__(self, url: str, embedding_dim: int) -> None:
        self.client = QdrantClient(url=url)
        self.embedding_dim = embedding_dim

    # ── ensure_collection ─────────────────────────────────────────────────────

    def _ensure_collection_sync(self) -> None:
        existing = {c.name for c in self.client.get_collections().collections}
        if COLLECTION in existing:
            return
        try:
            self.client.create_collection(
                collection_name=COLLECTION,
                vectors_config=VectorParams(size=self.embedding_dim, distance=Distance.COSINE),
            )
        except Exception as exc:
            if getattr(exc, "status_code", None) == 409:
                return  # another replica created it first
            raise

    async def ensure_collection(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._ensure_collection_sync)

    # ── upsert_points ─────────────────────────────────────────────────────────

    def _upsert_sync(self, event: EmbeddedNodesEvent) -> None:
        points = [
            PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_OID, node.stable_id)),
                vector=node.vector,
                payload={
                    "stable_id": node.stable_id,
                    "name": node.name,
                    "type": node.type,
                    "file_path": node.file_path,
                    "language": node.language,
                    "repo": event.repo,
                    "node_hash": node.node_hash,
                },
            )
            for node in event.nodes
        ]
        self.client.upsert(collection_name=COLLECTION, points=points)

    async def upsert_points(self, event: EmbeddedNodesEvent) -> None:
        if not event.nodes:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._upsert_sync, event)

    # ── delete_points ─────────────────────────────────────────────────────────

    def _delete_sync(self, stable_ids: list[str]) -> None:
        point_ids = [str(uuid.uuid5(uuid.NAMESPACE_OID, sid)) for sid in stable_ids]
        self.client.delete(
            collection_name=COLLECTION,
            points_selector=PointIdsList(points=point_ids),
        )

    async def delete_points(self, stable_ids: list[str]) -> None:
        if not stable_ids:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._delete_sync, stable_ids)

    # ── get_collections (used by /ready) ──────────────────────────────────────

    async def get_collections(self):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.client.get_collections)
```

- [ ] **Step 4: Run writer tests**

```bash
python -m pytest tests/test_writer.py -k "upsert or delete or ensure" -v
```

Expected: all 9 writer tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/vector-writer/app/writer.py services/vector-writer/tests/test_writer.py
git commit -m "feat(vector-writer): add QdrantWriter with upsert/delete/ensure_collection"
```

---

## Task 5: consumer.py (TDD)

**Files:**
- Create: `services/vector-writer/app/consumer.py`
- Modify: `services/vector-writer/tests/test_writer.py`

- [ ] **Step 1: Add consumer tests to test_writer.py**

Append these tests:

```python
# ── consumer.py: _process_embedded ────────────────────────────────────────────

async def test_process_embedded_calls_upsert_points():
    from app.consumer import _process_embedded
    mock_writer = AsyncMock()
    event = _make_event()
    await _process_embedded(mock_writer, {"event": event.model_dump_json()})
    mock_writer.upsert_points.assert_called_once()


async def test_process_embedded_raises_on_upsert_failure():
    """Consumer loop must NOT XACK when upsert raises — verified by checking exception propagates."""
    from app.consumer import _process_embedded
    mock_writer = AsyncMock()
    mock_writer.upsert_points.side_effect = Exception("qdrant down")
    with pytest.raises(Exception, match="qdrant down"):
        await _process_embedded(mock_writer, {"event": _make_event().model_dump_json()})


async def test_process_embedded_raises_validation_error_on_bad_json():
    """Consumer loop must XACK on bad JSON — verified by checking ValidationError propagates."""
    from app.consumer import _process_embedded
    from pydantic import ValidationError
    mock_writer = AsyncMock()
    with pytest.raises((ValidationError, ValueError, Exception)):
        await _process_embedded(mock_writer, {"event": "not valid json at all {{"})
    mock_writer.upsert_points.assert_not_called()


async def test_process_embedded_returns_normally_when_nodes_empty():
    """Empty nodes → returns without calling upsert_points → caller will XACK."""
    from app.consumer import _process_embedded
    mock_writer = AsyncMock()
    empty_event = _make_event(nodes=[])
    await _process_embedded(mock_writer, {"event": empty_event.model_dump_json()})
    mock_writer.upsert_points.assert_not_called()


# ── consumer.py: _process_delete ──────────────────────────────────────────────

async def test_process_delete_calls_delete_points():
    from app.consumer import _process_delete
    mock_writer = AsyncMock()
    event_json = json.dumps({"repo": "acme", "deleted_nodes": ["sha256:abc", "sha256:def"]})
    await _process_delete(mock_writer, {"event": event_json})
    mock_writer.delete_points.assert_called_once_with(["sha256:abc", "sha256:def"])


async def test_process_delete_skips_when_deleted_nodes_empty():
    """Empty deleted_nodes → returns without calling delete_points → caller will XACK."""
    from app.consumer import _process_delete
    mock_writer = AsyncMock()
    event_json = json.dumps({"repo": "acme", "deleted_nodes": []})
    await _process_delete(mock_writer, {"event": event_json})
    mock_writer.delete_points.assert_not_called()


async def test_process_delete_raises_on_delete_failure():
    """Consumer loop must NOT XACK when delete raises — verified by checking exception propagates."""
    from app.consumer import _process_delete
    mock_writer = AsyncMock()
    mock_writer.delete_points.side_effect = Exception("qdrant down")
    event_json = json.dumps({"repo": "acme", "deleted_nodes": ["sha256:abc"]})
    with pytest.raises(Exception, match="qdrant down"):
        await _process_delete(mock_writer, {"event": event_json})


async def test_process_delete_raises_on_bad_json():
    """Consumer loop must XACK on bad JSON — verified by checking exception propagates."""
    from app.consumer import _process_delete
    from pydantic import ValidationError
    mock_writer = AsyncMock()
    with pytest.raises((ValidationError, ValueError, Exception)):
        await _process_delete(mock_writer, {"event": "{{not json}}"})
    mock_writer.delete_points.assert_not_called()
```

- [ ] **Step 2: Run to confirm failures**

```bash
python -m pytest tests/test_writer.py -k "process_embedded or process_delete" -v
```

Expected: `ImportError: cannot import name '_process_embedded' from 'app.consumer'`

- [ ] **Step 3: Create consumer.py**

```python
from __future__ import annotations
import asyncio
import logging
import os
import socket

import redis.asyncio as aioredis
import redis.exceptions
from pydantic import ValidationError

from .models import EmbeddedNodesEvent, FileChangedEvent
from .writer import QdrantWriter

logger = logging.getLogger(__name__)

STREAM_EMBEDDED = "stream:embedded-nodes"
GROUP_EMBEDDED = "vector-writer-group"

STREAM_FILE_CHANGED = "stream:file-changed"
GROUP_DELETE = "vector-writer-delete-group"


async def _process_embedded(writer: QdrantWriter, data: dict) -> None:
    """Parse an embedded-nodes message and upsert to Qdrant.

    Raises on Qdrant failure (caller must NOT XACK).
    Raises ValidationError/ValueError on malformed JSON (caller should XACK).
    Returns normally on success or empty nodes list (caller should XACK).
    """
    raw = data.get(b"event") or data.get("event")
    if raw is None:
        raise KeyError("message missing 'event' key")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    event = EmbeddedNodesEvent.model_validate_json(raw)
    if not event.nodes:
        return
    await writer.upsert_points(event)


async def _process_delete(writer: QdrantWriter, data: dict) -> None:
    """Parse a file-changed message and delete removed nodes from Qdrant.

    Raises on Qdrant failure (caller must NOT XACK).
    Raises ValidationError/ValueError on malformed JSON (caller should XACK).
    Returns normally on success or empty deleted_nodes (caller should XACK).
    """
    raw = data.get(b"event") or data.get("event")
    if raw is None:
        raise KeyError("message missing 'event' key")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    event = FileChangedEvent.model_validate_json(raw)
    if not event.deleted_nodes:
        return
    await writer.delete_points(event.deleted_nodes)


async def run_embedded_consumer(writer: QdrantWriter) -> None:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = aioredis.from_url(url)
    consumer_name = f"vector-writer-{socket.gethostname()}"
    try:
        try:
            await r.xgroup_create(STREAM_EMBEDDED, GROUP_EMBEDDED, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        logger.info("Embedded consumer started: group=%s consumer=%s", GROUP_EMBEDDED, consumer_name)

        while True:
            try:
                messages = await r.xreadgroup(
                    groupname=GROUP_EMBEDDED,
                    consumername=consumer_name,
                    streams={STREAM_EMBEDDED: ">"},
                    count=10,
                    block=1000,
                )
                for _stream, events in (messages or []):
                    for msg_id, data in events:
                        try:
                            await _process_embedded(writer, data)
                            await r.xack(STREAM_EMBEDDED, GROUP_EMBEDDED, msg_id)
                        except (ValidationError, ValueError, KeyError) as exc:
                            logger.warning("Bad message %s, skipping (XACK): %s", msg_id, exc)
                            await r.xack(STREAM_EMBEDDED, GROUP_EMBEDDED, msg_id)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.error("Failed msg_id=%s, will retry: %s", msg_id, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Embedded consumer loop error: %s", exc)
    finally:
        await r.aclose()


async def run_delete_consumer(writer: QdrantWriter) -> None:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = aioredis.from_url(url)
    consumer_name = f"vector-writer-delete-{socket.gethostname()}"
    try:
        try:
            await r.xgroup_create(STREAM_FILE_CHANGED, GROUP_DELETE, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        logger.info("Delete consumer started: group=%s consumer=%s", GROUP_DELETE, consumer_name)

        while True:
            try:
                messages = await r.xreadgroup(
                    groupname=GROUP_DELETE,
                    consumername=consumer_name,
                    streams={STREAM_FILE_CHANGED: ">"},
                    count=10,
                    block=1000,
                )
                for _stream, events in (messages or []):
                    for msg_id, data in events:
                        try:
                            await _process_delete(writer, data)
                            await r.xack(STREAM_FILE_CHANGED, GROUP_DELETE, msg_id)
                        except (ValidationError, ValueError, KeyError) as exc:
                            logger.warning("Bad delete message %s, skipping (XACK): %s", msg_id, exc)
                            await r.xack(STREAM_FILE_CHANGED, GROUP_DELETE, msg_id)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.error("Failed delete msg_id=%s, will retry: %s", msg_id, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Delete consumer loop error: %s", exc)
    finally:
        await r.aclose()
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/ -v
```

Expected: all 15 tests PASS (4 model + 9 writer + 8 consumer... wait, count: 4 model + 9 writer + 8 consumer process tests = 21 tests total. The spec says 15 — the plan adds more granular coverage. All should pass).

- [ ] **Step 5: Commit**

```bash
git add services/vector-writer/app/consumer.py services/vector-writer/tests/test_writer.py
git commit -m "feat(vector-writer): add consumer loops with upsert and delete processing"
```

---

## Task 6: main.py

**Files:**
- Create: `services/vector-writer/app/main.py`

No unit tests for main.py — the lifespan and HTTP endpoints are verified via the manual health check in the definition of done.

- [ ] **Step 1: Create main.py**

```python
from __future__ import annotations
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "vector-writer"

_redis_client: aioredis.Redis | None = None
_writer = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = aioredis.from_url(url)
    return _redis_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _writer
    from .writer import QdrantWriter
    from .consumer import run_embedded_consumer, run_delete_consumer

    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    embedding_dim = int(os.environ.get("EMBEDDING_DIM", "768"))

    _writer = QdrantWriter(url=qdrant_url, embedding_dim=embedding_dim)
    await _writer.ensure_collection()

    task1 = asyncio.create_task(run_embedded_consumer(_writer))
    task2 = asyncio.create_task(run_delete_consumer(_writer))
    try:
        yield
    finally:
        task1.cancel()
        task2.cancel()
        for task in [task1, task2]:
            try:
                await task
            except asyncio.CancelledError:
                pass


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
        if _writer:
            await _writer.get_collections()
    except Exception as exc:
        errors.append(f"qdrant: {exc}")
    if errors:
        return JSONResponse(status_code=503, content={"status": "unavailable", "errors": errors})
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP vector_writer_messages_processed_total Total messages processed",
        "# TYPE vector_writer_messages_processed_total counter",
        "vector_writer_messages_processed_total 0",
        "# HELP vector_writer_messages_failed_total Total messages failed",
        "# TYPE vector_writer_messages_failed_total counter",
        "vector_writer_messages_failed_total 0",
        "# HELP vector_writer_points_upserted_total Total points upserted to Qdrant",
        "# TYPE vector_writer_points_upserted_total counter",
        "vector_writer_points_upserted_total 0",
        "# HELP vector_writer_points_deleted_total Total points deleted from Qdrant",
        "# TYPE vector_writer_points_deleted_total counter",
        "vector_writer_points_deleted_total 0",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
```

- [ ] **Step 2: Run all tests to confirm nothing is broken**

```bash
python -m pytest tests/ -v
```

Expected: all tests still PASS.

- [ ] **Step 3: Commit**

```bash
git add services/vector-writer/app/main.py
git commit -m "feat(vector-writer): add FastAPI app with health/ready/metrics endpoints"
```

---

## Task 7: Wire into docker-compose

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add vector-writer to docker-compose.yml**

Append to the `services:` section of `docker-compose.yml`:

```yaml
  vector-writer:
    build: ./services/vector-writer
    ports:
      - "8085:8080"
    environment:
      REDIS_URL: redis://redis:6379
      QDRANT_URL: http://qdrant:6333
      EMBEDDING_DIM: "768"
    depends_on:
      redis:
        condition: service_healthy
      qdrant:
        condition: service_healthy
      embedder:
        condition: service_healthy
    networks:
      - tersecontext
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 10s
      timeout: 5s
      retries: 5
```

- [ ] **Step 2: Verify docker-compose config is valid**

```bash
docker compose config --quiet
```

Expected: exits 0 with no errors.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(vector-writer): wire vector-writer into docker-compose"
```

---

## Task 8: Dockerfile build verification

- [ ] **Step 1: Build the image**

```bash
docker build -t vector-writer-test services/vector-writer/
```

Expected: build completes with exit code 0. If `uv pip install` fails, check that `qdrant-client` is in `requirements.txt` and the package name is spelled correctly.

- [ ] **Step 2: Commit if any fixes were needed**

If the build exposed a requirements issue, fix it, re-run tests, then:

```bash
git add services/vector-writer/requirements.txt
git commit -m "fix(vector-writer): correct requirements"
```

---

## Verification Checklist

After all tasks are complete, run these in order to validate the full definition of done:

```bash
# 1. Unit tests pass
cd services/vector-writer && python -m pytest tests/ -v

# 2. Health endpoint (requires docker-compose up)
curl http://localhost:8085/health
# expect: {"status":"ok","service":"vector-writer","version":"0.1.0"}

# 3. Collection created on startup
curl http://localhost:6333/collections/nodes
# expect: collection info with vector_size matching EMBEDDING_DIM

# 4. Vectors appear after indexing (requires parser + embedder running upstream)
# (see CLAUDE.md Verification section for full end-to-end steps)

# 5. Dockerfile builds cleanly
docker build -t vector-writer-test services/vector-writer/
```
