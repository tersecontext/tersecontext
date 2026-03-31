# Vector Writer Service Design

**Date:** 2026-03-29
**Service:** `services/vector-writer`
**Status:** Approved

---

## Overview

The vector-writer consumes `EmbeddedNodes` events from Redis and writes vectors to Qdrant. It runs entirely in parallel with graph-writer — both read `stream:embedded-nodes`, but write to different stores (Qdrant vs Neo4j). It also listens to `stream:file-changed` for delete events.

- **Port:** 8085 (internal :8080, mapped 8085:8080)
- **Language:** Python 3.12
- **Input streams:** `stream:embedded-nodes`, `stream:file-changed`
- **Writes to:** Qdrant `nodes` collection

---

## Upstream Contract Change (Embedder)

The `embedded_nodes` event contract must be extended before vector-writer can be built. The Qdrant payload requires `name`, `type`, `file_path`, `language` per node — fields not currently in the event.

**Files to update:**

1. `contracts/events/embedded_nodes.json` — add `name`, `type`, `file_path`, `language` as required fields on each node item, in both `required` and `properties`. The node item schema already declares `"additionalProperties": false`, so fields must be explicitly added there.
2. `services/embedder/app/models.py` — add `name: str`, `type: str`, `file_path: str`, `language: str` to `EmbeddedNode`.
3. `services/embedder/app/embedder.py` — copy these four fields from `ParsedNode` when constructing each `EmbeddedNode`.
4. `services/embedder/tests/test_embedder.py` — update fixtures to include the new fields.

**Deployment ordering:** The contract JSON update must be deployed atomically with or before the embedder change. Any consumer that validates against the old contract will reject the extended event until the schema is updated. In practice the contract file and embedder are updated in the same commit; consumers that use Pydantic with `extra="ignore"` will not break on the new fields.

---

## Architecture Decision

Two independent asyncio tasks (one per stream) share a single sync `QdrantClient` instance. Qdrant calls go through `loop.run_in_executor` — the same pattern used for Neo4j in the embedder. This gives failure isolation between the two streams: a crash in the delete loop does not affect upserts, and vice versa.

`AsyncQdrantClient` was considered but rejected — the sync client is battle-tested and the executor overhead is negligible for this workload.

---

## Qdrant Collection Spec

- **Collection name:** `nodes`
- **Created on startup** if it does not exist
- **Vector size:** `EMBEDDING_DIM` env var (default 768 for nomic-embed-text, 1024 for voyage-code-3)
- **Distance:** Cosine

**Point structure:**
```json
{
  "id": "<uuid5(NAMESPACE_OID, stable_id)>",
  "vector": [0.023, ...],
  "payload": {
    "stable_id": "sha256:...",
    "name": "authenticate",
    "type": "function",
    "file_path": "auth/service.py",
    "language": "python",
    "repo": "acme-api",
    "node_hash": "sha256:..."
  }
}
```

`stable_id` is included in the payload so that any service receiving a Qdrant result can immediately use it for a Neo4j lookup without additional computation. Full node properties live in Neo4j; the Qdrant payload is the minimal set needed to identify and retrieve the node.

Point IDs are deterministic: `str(uuid.uuid5(uuid.NAMESPACE_OID, stable_id))`. This ensures upsert idempotency — same stable_id always maps to the same UUID.

---

## File Structure

```
services/vector-writer/
  app/
    main.py         FastAPI app — /health, /ready, /metrics; starts two consumer tasks
    consumer.py     Two async loops: embedded-nodes (upsert) + file-changed (delete)
    writer.py       QdrantWriter: ensure_collection, upsert_points, delete_points
    models.py       Pydantic models: EmbeddedNodesEvent, EmbeddedNode, FileChangedEvent
  tests/
    test_writer.py
  Dockerfile
  requirements.txt
```

---

## Components

### `models.py`

Pydantic models mirroring the updated contracts.

- `EmbeddedNode` — `stable_id`, `vector`, `name`, `type`, `file_path`, `language`, `node_hash`
- `EmbeddedNodesEvent` — `repo`, `commit_sha: str | None = None`, `nodes: list[EmbeddedNode]`. `commit_sha` is optional — full-rescan events may not be tied to a specific commit; the vector-writer does not use this field.
- `FileChangedEvent` — `repo`, `deleted_nodes: list[str] = []`; uses `extra="ignore"` (only these two fields are used). Defaulting `deleted_nodes` to `[]` handles valid upstream events that omit the field rather than treating them as malformed. `deleted_nodes` is acted on regardless of `diff_type` — any event with a non-empty `deleted_nodes` triggers a Qdrant delete.

### `writer.py`

`QdrantWriter` wraps a sync `QdrantClient`. Constructor accepts the Qdrant URL and `embedding_dim`.

**`ensure_collection()`** — called at startup. Calls `client.get_collections()` to check if `nodes` exists; creates it with Cosine distance and `embedding_dim` vector size if absent. If `create_collection` raises a `CollectionAlreadyExistsException` (possible if two replicas start simultaneously), the exception is silently ignored. Wrapped in `run_in_executor`.

**`upsert_points(event: EmbeddedNodesEvent)`** — converts each node to a `PointStruct`:
- `id = str(uuid.uuid5(uuid.NAMESPACE_OID, node.stable_id))`
- `vector = node.vector`
- `payload = {stable_id, name, type, file_path, language, repo, node_hash}`

Calls `client.upsert(collection_name="nodes", points=[...])`. Wrapped in `run_in_executor`.

**`delete_points(stable_ids: list[str])`** — derives UUIDs as `[str(uuid.uuid5(uuid.NAMESPACE_OID, sid)) for sid in stable_ids]` (strings, matching the upsert path) and calls `client.delete(collection_name="nodes", points_selector=PointIdsList(points=[...]))`. Wrapped in `run_in_executor`.

**`get_collections()`** — thin wrapper around `client.get_collections()`. Used by the `/ready` endpoint to probe Qdrant connectivity. Wrapped in `run_in_executor`.

### `consumer.py`

Two independent async functions, each a XREADGROUP loop:

**`run_embedded_consumer(writer)`**
- Consumer group: `vector-writer-group`
- Consumer name: `vector-writer-{socket.gethostname()}`
- On startup: `xgroup_create` with `mkstream=True`, ignores `BUSYGROUP` error
- On each message: parse `EmbeddedNodesEvent`, skip if `nodes` is empty, call `writer.upsert_points`, XACK

**`run_delete_consumer(writer)`**
- Consumer group: `vector-writer-delete-group`
- Consumer name: `vector-writer-delete-{socket.gethostname()}`
- On startup: `xgroup_create` for `stream:file-changed` with `mkstream=True`, ignores `BUSYGROUP` error
- On each message: parse `FileChangedEvent`, skip if `deleted_nodes` is empty, call `writer.delete_points`, XACK

### `main.py`

FastAPI with lifespan context manager:

- Creates `QdrantWriter` on startup; calls `await ensure_collection()`
- Launches both consumer tasks as `asyncio.create_task`
- `GET /health` → `{"status": "ok", "service": "vector-writer", "version": "0.1.0"}`
- `GET /ready` → 200 if Redis ping + Qdrant `get_collections()` succeed, 503 otherwise. The Qdrant probe calls `writer.get_collections()` via `loop.run_in_executor` (consistent with the rule that all sync Qdrant calls are executor-wrapped; never called directly from the async route handler).
- `GET /metrics` → Prometheus-format text stub with counters:
  - `vector_writer_messages_processed_total`
  - `vector_writer_messages_failed_total`
  - `vector_writer_points_upserted_total`
  - `vector_writer_points_deleted_total`

---

## Data Flow

```
stream:embedded-nodes (vector-writer-group)
        │
        ▼
for each message:
  parse EmbeddedNodesEvent
  if nodes is empty → XACK, continue
  upsert_points(event) via run_in_executor
    → str(uuid5(NAMESPACE_OID, stable_id)) per node
    → PointStruct(id=uuid_str, vector=vector,
                  payload={stable_id,name,type,file_path,language,repo,node_hash})
    → client.upsert(collection_name="nodes", points=[...])
  XACK

stream:file-changed (vector-writer-delete-group)
        │
        ▼
for each message:
  parse FileChangedEvent
  if deleted_nodes is empty → XACK, continue (regardless of diff_type)
  delete_points(deleted_nodes) via run_in_executor
    → [str(uuid5(NAMESPACE_OID, sid)) for sid in stable_ids]
    → client.delete(collection_name="nodes", points_selector=PointIdsList(...))
  XACK
```

**Key invariants:**
- XACK only after successful Qdrant write
- On Qdrant failure: log error, do NOT XACK (allow retry)
- On malformed JSON: log warning, XACK (bad message; retrying cannot fix corrupt data)
- Upsert is idempotent — same stable_id maps to same UUID; Qdrant upsert overwrites

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Malformed JSON (either stream) | Log warning, XACK (bad message; do not retry) |
| Qdrant upsert fails | Log error, do NOT XACK (retry on next poll) |
| Qdrant delete fails | Log error, do NOT XACK |
| Empty `nodes` list | XACK normally (nothing to write) |
| Empty `deleted_nodes` | XACK normally (nothing to delete) |
| Qdrant unreachable at `/ready` | Return 503 |
| Collection missing at startup | `ensure_collection` creates it |
| `CollectionAlreadyExistsException` at startup | Silently ignored (race between replicas) |

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant base URL |
| `EMBEDDING_DIM` | `768` | Vector dimension for collection creation |

---

## Testing

All tests in `tests/test_writer.py`. No live services — `QdrantClient` mocked via `unittest.mock`.

| # | Test |
|---|---|
| 1 | `upsert_points` — calls `client.upsert` with correct collection name |
| 2 | `upsert_points` — point IDs are deterministic `str(uuid5(NAMESPACE_OID, stable_id))` |
| 3 | `upsert_points` — payload contains `stable_id`, `name`, `type`, `file_path`, `language`, `repo`, `node_hash` |
| 4 | `upsert_points` — empty nodes list → `client.upsert` not called |
| 5 | `delete_points` — calls `client.delete` with correct UUIDs (as strings) derived from stable_ids |
| 6 | `delete_points` — empty stable_ids list → `client.delete` not called |
| 7 | `ensure_collection` — calls `client.create_collection` when collection absent |
| 8 | `ensure_collection` — does not call `client.create_collection` when collection exists |
| 9 | `ensure_collection` — silently ignores `CollectionAlreadyExistsException` |
| 10 | Upsert consumer skips XACK on Qdrant upsert failure |
| 11 | Upsert consumer XACKs malformed JSON without calling upsert |
| 12 | Delete consumer skips events where `deleted_nodes` is empty |
| 13 | Delete consumer skips XACK on Qdrant delete failure |
| 14 | Delete consumer XACKs malformed JSON without calling delete |
| 15 | Upsert consumer XACKs and does not call upsert when `nodes` is empty |

---

## Dockerfile

Follows the embedder pattern:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
COPY requirements.txt ./
RUN uv pip install --system -r requirements.txt
COPY app/ app/
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

---

## docker-compose addition

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

---

## Definition of Done

- [ ] `/health` returns 200
- [ ] `nodes` collection created on startup with correct vector_size
- [ ] Vectors appear in Qdrant after processing `embedded-nodes` events
- [ ] Semantic search returns relevant results
- [ ] Delete removes points from Qdrant
- [ ] Idempotent — same embeddings twice = same collection state
- [ ] All unit tests pass
- [ ] Dockerfile builds cleanly
