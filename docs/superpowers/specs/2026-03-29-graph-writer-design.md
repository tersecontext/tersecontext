# Graph Writer Service Design

**Date:** 2026-03-29
**Service:** `services/graph-writer`
**Status:** Approved

---

## Overview

The graph-writer is one of two consumers of `stream:embedded-nodes` (the other is vector-writer). It is the **only** service that writes nodes and edges to Neo4j. It also consumes `stream:parsed-file` for edge upserts and delete events.

- **Port:** 8084 (internal :8080, mapped 8084:8080)
- **Language:** Python 3.12
- **Input streams:** `stream:embedded-nodes` (node upserts), `stream:parsed-file` (edges + deletes)
- **Writes to:** Neo4j

---

## Upstream Changes Required

> **Prerequisites:** These changes must be applied and deployed before the graph-writer node consumer is enabled. Without the embedder change, every `EmbeddedNodesEvent` message will fail `ValidationError` (missing required `file_path` field). Without the parser change, `qualified_name` will be stored as `""` on all Neo4j nodes until the parser is redeployed.

Two changes to upstream services are needed before this service can be implemented:

### 1. Parser — add `qualified_name` to `ParsedNode`

`services/parser/app/models.py` — add field:
```python
qualified_name: str = ""
```

`services/parser/app/extractor.py` — populate it per node type:
- function: `fn_name`
- class: `cls_name`
- method: `f"{cls_name}.{m_name}"`
- import: `name`

This is the same value used by the extractor to compute `stable_id`. Storing it on the node makes the graph queryable by qualified name.

### 2. Embedder — add `file_path` to `EmbeddedNodesEvent`

`services/embedder/app/models.py` — add field to `EmbeddedNodesEvent`:
```python
file_path: str
```

`services/embedder/app/consumer.py` — populate from the `ParsedFileEvent`:
```python
out = EmbeddedNodesEvent(
    repo=event.repo,
    commit_sha=event.commit_sha,
    file_path=event.file_path,   # ← add this
    nodes=embedded,
)
```

This is required because multiple files in a commit share the same `commit_sha`. The graph-writer cache must be keyed by `(commit_sha, file_path)` to avoid collision.

---

## Architecture Decision

Two independent async consumer loops run as background tasks in `main.py`. They share a module-level `dict[tuple[str,str], ParsedFileEvent]` cache keyed by `(commit_sha, file_path)`. The edge consumer populates the cache; the node consumer reads from it.

The cache resolves the data gap between streams: `EmbeddedNodesEvent` carries only `stable_id`, `vector`, `embed_text`, `node_hash` — full node metadata (`name`, `type`, `signature`, etc.) lives in `ParsedFileEvent`. Since the embedder processes `parsed-file` events to produce `embedded-nodes` events, `parsed-file` always arrives first, so cache misses are exceptional (restart / out-of-order replay scenarios only).

All Neo4j writes use the sync bolt driver called via `loop.run_in_executor`. All upserts use `UNWIND` batching — one round-trip per event.

---

## Data Flow

```
stream:parsed-file  ──► edge_consumer  (group: graph-writer-edges-group)
                          │  cache[(commit_sha, file_path)] = ParsedFileEvent
                          │  writer.upsert_edges(driver, edges)
                          │  writer.tombstone(driver, deleted_nodes)   ← if non-empty
                          └► XACK

stream:embedded-nodes ──► node_consumer  (group: graph-writer-group)
                          │  look up cache[(commit_sha, file_path)]
                          │  merge EmbeddedNode + ParsedNode → NodeRecord
                          │  writer.upsert_nodes(driver, records)
                          └► XACK
```

**Key invariants:**
- XACK only after successful write
- On Neo4j write failure: log error, skip XACK (allow retry)
- On malformed JSON / ValidationError: log warning, XACK (bad message; retry cannot help)
- On cache miss in node consumer: log warning, XACK (unrecoverable without re-parse)
- `tombstone()` is a no-op when `deleted_nodes` is empty (not called)
- Individual node missing from cache within a batch: log warning per node, skip that node, continue with the rest

---

## File Structure

```
services/graph-writer/
  app/
    main.py        FastAPI app — /health, /ready, /metrics; starts two consumer tasks
    consumer.py    Two async consumer loops + shared in-memory node cache
    writer.py      All Neo4j write logic (upsert_nodes, upsert_edges, tombstone)
    models.py      Pydantic models for both input streams
  tests/
    test_writer.py
  Dockerfile
  requirements.txt
```

---

## Components

### `models.py`

> **Note:** `ParsedFileEvent` here differs from the embedder's copy — it includes `deleted_nodes`. `extra="ignore"` is declared explicitly (not relied on as a Pydantic v2 default) for forward-compatibility.

```python
class ParsedNode(BaseModel):
    stable_id: str
    node_hash: str
    type: str
    name: str
    qualified_name: str = ""   # populated by parser extractor; see upstream changes
    signature: str
    docstring: str = ""
    body: str
    line_start: int
    line_end: int
    parent_id: Optional[str] = None

class IntraFileEdge(BaseModel):
    source_stable_id: str
    target_stable_id: str
    type: Literal["CALLS"]   # only supported edge type; Cypher hardcodes CALLS relationship

class ParsedFileEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repo: str
    commit_sha: str
    file_path: str
    language: str
    nodes: list[ParsedNode]
    intra_file_edges: list[IntraFileEdge]
    deleted_nodes: list[str] = []   # ← NOT in embedder's copy; required here for tombstones

class EmbeddedNode(BaseModel):
    stable_id: str
    vector: list[float]
    embed_text: str
    node_hash: str

class EmbeddedNodesEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repo: str
    commit_sha: str
    file_path: str   # ← requires upstream embedder change; used as cache key
    nodes: list[EmbeddedNode]
```

`qualified_name` defaults to `""`. After the upstream parser change it will always be populated. If it arrives empty (stale parser deployment), the node consumer falls back to `node.name` — the bare name the extractor uses when computing `stable_id` for top-level nodes.

### `writer.py`

Wraps `neo4j.GraphDatabase.driver` (sync bolt driver). Three public methods, each called via `loop.run_in_executor`:

**`upsert_nodes(driver, nodes: list[dict]) -> None`**

```cypher
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
```

Empty list → no driver call.

**`upsert_edges(driver, edges: list[dict]) -> None`**

```cypher
UNWIND $edges AS e
MATCH (a:Node { stable_id: e.source }), (b:Node { stable_id: e.target })
MERGE (a)-[r:CALLS]->(b)
SET r.source = 'static', r.updated_at = datetime()
```

Empty list → no driver call. Uses `MATCH` (not `MERGE`) for endpoints: if either node is absent, that row is silently skipped by Neo4j. The `edges_written` metric counts rows passed in, not confirmed writes (Neo4j batch UNWIND does not return per-row match counts).

**`tombstone(driver, stable_ids: list[str]) -> None`**

```cypher
MATCH (n:Node) WHERE n.stable_id IN $stable_ids
SET n.active = false, n.deleted_at = datetime()
WITH n
MATCH (n)-[r]->() DELETE r
WITH n
MATCH ()-[r]->(n) DELETE r
```

Sets `active=false` AND removes edges atomically. Hard-delete of the node is not performed — BehaviorSpecs may reference it. Empty list → no driver call.

### `consumer.py`

Module-level cache:
```python
_parsed_file_cache: dict[tuple[str, str], ParsedFileEvent] = {}
#                        (commit_sha,   file_path)
```

**`run_edge_consumer()`** — consumer group `graph-writer-edges-group`:
1. Parse `ParsedFileEvent` from message
2. Store in `_parsed_file_cache[(event.commit_sha, event.file_path)]`
3. Call `writer.upsert_edges(driver, edges)`
4. If `event.deleted_nodes`: call `writer.tombstone(driver, event.deleted_nodes)`
5. XACK

**`run_node_consumer()`** — consumer group `graph-writer-group`:
1. Parse `EmbeddedNodesEvent` from message
2. Look up `_parsed_file_cache[(event.commit_sha, event.file_path)]`; on miss: log warning, XACK, continue
3. Build lookup `{stable_id: ParsedNode}` from cached event
4. For each `EmbeddedNode`: find matching `ParsedNode` by `stable_id`; on miss: log warning, skip node
5. Build merged records and call `writer.upsert_nodes(driver, records)`
6. XACK

Consumer name: `graph-writer-{socket.gethostname()}` (both consumers use the same host suffix but different groups).

### `main.py`

FastAPI with lifespan context manager. Starts both consumer tasks on startup, cancels and awaits them on shutdown.

- `GET /health` → `{"status": "ok", "service": "graph-writer", "version": "0.1.0"}`
- `GET /ready` → 200 if Redis ping + Neo4j `verify_connectivity()` succeed, 503 otherwise
- `GET /metrics` → Prometheus-format text stub (counters: messages_processed, messages_failed, nodes_written, edges_written, tombstones_written)

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Neo4j write fails (transient) | Log error, skip XACK → retry on next poll |
| Malformed JSON / ValidationError | Log warning, XACK → bad message, retry cannot help |
| Cache miss (whole event) in node consumer | Log warning, XACK → unrecoverable without re-parse |
| Individual node not found in cache lookup | Log warning, skip that node, continue with rest |
| `deleted_nodes` is empty | `tombstone()` not called |
| `nodes` is empty in embedded-nodes event | `upsert_nodes()` called with `[]` → no-op |

---

## Testing

All tests in `tests/test_writer.py`. No live services — Neo4j driver is mocked.

| # | Test |
|---|---|
| 1 | `upsert_nodes` — driver called with correct Cypher params; `active=True`, `updated_at` present |
| 2 | `upsert_nodes` — empty list → no driver call |
| 3 | `upsert_edges` — edges written with `source='static'` |
| 4 | `upsert_edges` — empty list → no driver call |
| 5 | `tombstone` — sets `active=False`, `deleted_at` present |
| 6 | `tombstone` — empty list → no driver call |
| 7 | `qualified_name` stored correctly when populated from parsed event |
| 8 | Partial cache hit — 3 nodes in event, 1 missing from cache → only 2 nodes passed to `upsert_nodes` |

Consumer XACK policy (cache miss → XACK, Neo4j failure → no XACK) is integration-level behaviour verified via the verification scripts in CLAUDE.md, not unit tested.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `NEO4J_URL` | `bolt://localhost:7687` | Neo4j bolt URL |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | — | Neo4j password |

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

## Definition of Done

- [ ] `/health` returns 200
- [ ] Nodes appear in Neo4j after processing embedded-nodes events
- [ ] MERGE is idempotent — same file twice = same node count
- [ ] CALLS edges written with `source='static'`
- [ ] Tombstone sets `active=false` AND removes edges atomically
- [ ] Consuming from both streams independently
- [ ] All unit tests pass
- [ ] Dockerfile builds cleanly
- [ ] Parser emits `qualified_name` in `ParsedNode`
- [ ] Embedder emits `file_path` in `EmbeddedNodesEvent`
