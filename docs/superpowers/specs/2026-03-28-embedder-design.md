# Embedder Service Design

**Date:** 2026-03-28
**Service:** `services/embedder`
**Status:** Approved

---

## Overview

The embedder sits between the parser (upstream) and the graph-writer + vector-writer (downstream). It consumes `ParsedFile` events from Redis, builds embed texts per node, skips unchanged nodes via Neo4j hash lookup, calls the configured embedding provider in batches, and emits `EmbeddedNodes` events.

- **Port:** 8083 (internal :8080, mapped 8083:8080)
- **Language:** Python 3.12
- **Input stream:** `stream:parsed-file`
- **Output stream:** `stream:embedded-nodes`
- **Reads from:** Neo4j (node hash lookup only)
- **Writes to:** nothing (event emission only)

---

## Architecture Decision

The consumer is **fully async** — uses `redis.asyncio`, async embedding providers via `httpx`, and the sync Neo4j bolt driver called via `loop.run_in_executor`. This matches the async provider interface specified in CLAUDE.md and avoids threading complexity.

Neo4j hash lookups use a single **`UNWIND` batch query** per ParsedFile event rather than one query per node, reducing round-trips from N to 1.

`neo4j_client.py` is added as an intentional module beyond the minimal file list in CLAUDE.md. It keeps Neo4j I/O isolated from the pure-logic `embedder.py`, which simplifies testing.

---

## Data Flow

```
Redis stream:parsed-file
        │  (redis.asyncio XREADGROUP, count=10, block=1000ms)
        ▼
consumer.py  ─── for each message:
        │
        ├─ parse ParsedFileEvent from JSON
        │
        ├─ neo4j_client.py: UNWIND query → {stable_id → node_hash} cache dict
        │
        ├─ embedder.py: filter nodes (skip if hash matches cache)
        │                   │
        │                   ├─ build embed_text for each node-to-embed
        │                   ├─ chunk into batches of BATCH_SIZE (64)
        │                   └─ call provider.embed(batch) sequentially
        │
        ├─ build EmbeddedNodes event
        ├─ XADD stream:embedded-nodes
        └─ XACK input message
```

**Key invariants:**
- If all nodes are unchanged → emit `EmbeddedNodes` with `nodes: []` (not suppressed)
- XACK only after successful XADD
- On embedding failure: log, do NOT XACK (allow retry)
- On malformed JSON: log, XACK (bad message; retrying cannot fix corrupt data)

---

## File Structure

```
services/embedder/
  app/
    main.py            FastAPI app — /health, /ready, /metrics; starts consumer task
    consumer.py        Async Redis Stream consumer loop
    embedder.py        Pure logic: filter, build embed_text, batch, call provider
    neo4j_client.py    Sync Neo4j driver wrapped in run_in_executor; UNWIND batch query
    models.py          Pydantic models for ParsedFileEvent, EmbeddedNode, EmbeddedNodesEvent
    providers/
      base.py          Abstract EmbeddingProvider
      ollama.py        OllamaProvider — httpx POST to /api/embed
      voyage.py        VoyageProvider — httpx POST to Voyage AI REST API
  tests/
    test_embedder.py
  Dockerfile
  requirements.txt
```

---

## Components

### `models.py`

Pydantic models. `ParsedFileEvent` uses `model_config = ConfigDict(extra="ignore")` so extra fields in the stream (e.g. `deleted_nodes` emitted by the parser but absent from the formal JSON contract) are silently dropped rather than causing a validation error.

- `ParsedNode` — `stable_id`, `node_hash`, `name`, `signature`, `docstring: str` (empty string `""` when absent — never `None`), `body`, `type`, `line_start`, `line_end`, `parent_id: Optional[str]`
- `ParsedFileEvent` — `repo`, `commit_sha`, `file_path`, `language`, `nodes`, `intra_file_edges`
- `EmbeddedNode` — `stable_id`, `vector`, `embed_text`, `node_hash`
- `EmbeddedNodesEvent` — `repo`, `commit_sha`, `nodes`

> **`docstring` is always a string, never `None`.** The Pydantic field uses `str` with default `""`. The `build_embed_text` check `if node.docstring:` relies on this — an empty string is falsy, `None` would break the check silently.

### `providers/base.py`

```python
class EmbeddingProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
```

### `providers/ollama.py`

POSTs to `{OLLAMA_URL}/api/embed` using `httpx.AsyncClient`.

Request body:
```json
{"model": "nomic-embed-text", "input": ["text1", "text2"]}
```

Response — extract vectors from `response["embeddings"]` (list of float lists, one per input).

Model defaults to `nomic-embed-text`.

### `providers/voyage.py`

POSTs to `https://api.voyageai.com/v1/embeddings` with `Authorization: Bearer {VOYAGE_API_KEY}`.

Request body:
```json
{"model": "voyage-code-3", "input": ["text1", "text2"]}
```

Response — extract vectors from `response["data"][i]["embedding"]` for each `i`.

- Raises `ValueError` at `__init__` if `VOYAGE_API_KEY` is not set
- Model defaults to `voyage-code-3`

### `neo4j_client.py`

Wraps `neo4j.GraphDatabase.driver` (sync bolt driver). The `node_hash` property is stored on `:Node` nodes but is not indexed — this is acceptable because the `stable_id` unique constraint ensures O(1) lookup; `node_hash` is just a property read on the matched node.

Single method: `async def get_node_hashes(stable_ids: list[str]) -> dict[str, str]`

Runs one Cypher query via `loop.run_in_executor`:
```cypher
UNWIND $ids AS id
MATCH (n:Node {stable_id: id})
RETURN n.stable_id AS stable_id, n.node_hash AS node_hash
```

Returns `{stable_id: node_hash}` for all known nodes; absent entries = new nodes (embed them).

### `embedder.py`

Pure logic — no I/O, fully testable without live services:

```python
def build_embed_text(node: ParsedNode) -> str:
    parts = [node.name, node.signature]
    if node.docstring:
        parts.append(node.docstring)
    return " ".join(parts)
```

```python
async def embed_nodes(
    nodes: list[ParsedNode],
    neo4j_cache: dict[str, str],
    provider: EmbeddingProvider,
    batch_size: int = 64,
) -> list[EmbeddedNode]:
    # filter unchanged (skip if node.node_hash == neo4j_cache.get(node.stable_id))
    # build embed_texts for remaining nodes
    # chunk into batches of batch_size, call provider.embed() sequentially
    # assert len(vector) == EMBEDDING_DIM for each returned vector
    # return EmbeddedNode list
```

`EMBEDDING_DIM` is used for **validation**: after each batch, assert every returned vector has `len(vector) == EMBEDDING_DIM`. Raise `ValueError` if a vector has the wrong dimension. This catches misconfigured models early.

### `consumer.py`

- Async loop using `redis.asyncio`
- Consumer group: `embedder-group`
- Consumer name: `embedder-{socket.gethostname()}`
- XREADGROUP → `_process()` → XADD → XACK
- On embedding exception or XADD failure: log, skip XACK (allow retry)
- On JSON parse failure: log warning, XACK (bad message; do not retry)

### `main.py`

FastAPI with lifespan context manager:

- `GET /health` → `{"status": "ok", "service": "embedder", "version": "0.1.0"}`
- `GET /ready` → 200 if Redis ping + Neo4j `verify_connectivity()` succeed, 503 otherwise
- `GET /metrics` → Prometheus-format text stub
- Consumer started as `asyncio.create_task` on startup

---

## Embed Text Formula

```python
def build_embed_text(node) -> str:
    parts = [node.name, node.signature]
    if node.docstring:
        parts.append(node.docstring)
    return " ".join(parts)
```

Body is **never** included — it degrades embedding quality by diluting semantic signal.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `NEO4J_URL` | `bolt://localhost:7687` | Neo4j bolt URL |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | — | Neo4j password |
| `EMBEDDING_PROVIDER` | `ollama` | `"ollama"` or `"voyage"` |
| `OLLAMA_URL` | `http://ollama:11434` | Ollama base URL |
| `EMBEDDING_MODEL` | provider default | Override model name |
| `EMBEDDING_DIM` | `768` (ollama) / `1024` (voyage) | Expected vector dimension; used to validate returned vectors |
| `VOYAGE_API_KEY` | — | Required if provider=voyage |
| `BATCH_SIZE` | `64` | Nodes per embedding API call |

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Neo4j unreachable | Log **warning** with detail (so operators can detect degraded mode), treat all nodes as new (embed everything), continue. Accepted trade-off: downstream will re-upsert all nodes, which is idempotent. |
| Embedding API fails | Log error, do NOT XACK (retry on next poll) |
| Wrong vector dimension returned | Raise `ValueError`, do NOT XACK |
| Malformed event JSON | Log warning, XACK (bad message; retrying cannot fix corrupt data) |
| Empty nodes list | Emit `EmbeddedNodes` with `nodes: []`, XACK normally |
| Missing `VOYAGE_API_KEY` when provider=voyage | Raise `ValueError` at startup |

---

## Testing

All tests in `tests/test_embedder.py`. No live services — providers and Neo4j client are mocked.

| # | Test |
|---|---|
| 1 | `build_embed_text` — name + signature only when docstring is `""` |
| 2 | `build_embed_text` — appends docstring when non-empty |
| 3 | `build_embed_text` — never includes body |
| 4 | Batching — 130 nodes → 3 provider calls (64+64+2) |
| 5 | Skip unchanged — nodes whose hash matches cache excluded from output |
| 6 | New nodes (absent from cache) always embedded |
| 7 | All nodes unchanged → `EmbeddedNodes.nodes` is `[]` |
| 8 | Neo4j unreachable — all nodes treated as new (embed everything) |
| 9 | `OllamaProvider` satisfies `EmbeddingProvider` interface (instantiation check) |
| 10 | `VoyageProvider` satisfies `EmbeddingProvider` interface (instantiation check) |
| 11 | `VoyageProvider` raises `ValueError` at init if `VOYAGE_API_KEY` missing |

> **Consumer XACK policy** (malformed JSON → XACK; embedding failure → no XACK) is integration-level behaviour verified manually via the verification scripts in CLAUDE.md, not unit tested here.

---

## Dockerfile

Follows the `services/parser/Dockerfile` pattern:

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
- [ ] Processes `ParsedFile` and emits `EmbeddedNodes` with correct vector dimension
- [ ] `embed_text` does not contain full function body
- [ ] Batching confirmed — 130 nodes = 3 API calls not 130
- [ ] Skip-unchanged confirmed — same file twice = 0 embeddings on second run
- [ ] `OllamaProvider` and `VoyageProvider` both implemented and swappable via env
- [ ] All unit tests pass
- [ ] Dockerfile builds cleanly
