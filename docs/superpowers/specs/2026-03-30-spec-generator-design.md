# Spec Generator Service — Design

**Date:** 2026-03-30
**Service:** spec-generator
**Port:** 8097
**Branch:** feature/spec-generator

---

## Context

The spec-generator is the sixth and final dynamic analysis service in the TerseContext pipeline. It consumes `ExecutionPath` events produced by the trace-normalizer, renders structured `BehaviorSpec` documents from them, persists those documents to Postgres, and embeds them into Qdrant for semantic search retrieval.

Its output — the `behavior_specs` table and the `specs` Qdrant collection — feeds Layer 5 and Layer 6 of the context document produced by the serializer.

---

## Contract Corrections (prerequisite)

Before implementation, two contract files were corrected:

- `contracts/events/raw_trace.json` — added required `repo` field (trace-runner writes it from the EntrypointJob)
- `contracts/events/execution_path.json` — added required `repo` field (forwarded unchanged from RawTrace), and added `name` and `qualified_name` fields to each `call_sequence` item (trace-normalizer resolves these from the static graph)

These are corrections to incomplete contracts, not design changes.

---

## Architecture

Single Python/FastAPI service. One background asyncio task runs the Redis consumer loop. Three focused internal modules handle rendering, persistence, and embedding. No Neo4j dependency — all name resolution is done upstream by the trace-normalizer.

```
stream:execution-paths
        │
   consumer.py  (Redis consumer group: spec-generator-group)
        │
   renderer.py  (render_spec_text: ExecutionPath → str)
        │
    store.py    (SpecStore: Postgres upsert + Qdrant upsert)
        │
   ┌────┴────┐
Postgres   Qdrant
behavior_specs  specs collection
```

---

## Modules

### `models.py`

Pydantic models matching the updated `execution_path.json` contract:

- `CallSequenceItem` — `stable_id`, `name`, `qualified_name`, `hop`, `frequency_ratio`, `avg_ms`
- `SideEffect` — `type` (enum), `detail`, `hop_depth`
- `EdgeRef` — `source`, `target`
- `ExecutionPath` — full event model with `repo`, `entrypoint_stable_id`, `commit_sha`, all arrays, timing fields
- `BehaviorSpec` — internal representation before rendering: wraps ExecutionPath with rendered `spec_text`

### `renderer.py`

Pure function `render_spec_text(path: ExecutionPath, entrypoint_name: str) -> str`.

Renders three sections from the spec text format specified in CLAUDE.md:

**PATH section** — lists each call_sequence item with its `name`, `frequency_ratio`, and `avg_ms`. Items with `frequency_ratio < 1.0` are labelled with branch counts derived from the ratio and the run count approximation from `timing_p50_ms` context. The entrypoint name comes from the first call_sequence item's `name` (hop 0), falling back to `entrypoint_stable_id`.

**SIDE_EFFECTS section** — maps each side effect's `type` enum to its display prefix (`DB READ`, `DB WRITE`, `CACHE SET`, `HTTP OUT`, `ENV READ`, `FS WRITE`) and appends `detail`. Conditional side effects (hop_depth > 1) are annotated with `(conditional)`.

**CHANGE_IMPACT section** — derived from side effects: unique tables from DB reads/writes, unique external services from HTTP out.

No I/O. Fully testable without any mocks.

### `store.py`

`SpecStore` class. Constructed with a Postgres DSN, Qdrant URL, embedding provider, and embedding dim.

**`upsert_spec(path, spec_text) -> None`**

Executes an `INSERT ... ON CONFLICT (node_stable_id, repo, commit_sha) DO UPDATE` that increments `version` and overwrites `spec_text`, `branch_coverage`, `observed_calls`, `generated_at`. Uses `asyncpg` directly (no ORM).

`branch_coverage` is computed as the fraction of `call_sequence` items with `frequency_ratio == 1.0`.
`observed_calls` is `len(call_sequence)`.

**`upsert_qdrant(path, spec_text) -> None`**

Embeds `spec_text` using the configured embedding provider (same Ollama/Voyage abstraction as the embedder service). Upserts a single point to the `specs` collection with:

- `id`: `uuid5(NAMESPACE_OID, f"{path.entrypoint_stable_id}:{path.repo}")` — stable across reruns
- `vector`: embedding of `spec_text`
- `payload`: `{node_stable_id, entrypoint_name, repo, commit_sha}`

Creates the `specs` collection on startup if it does not exist (same pattern as vector-writer).

**`ensure_collection() -> None`** — called from lifespan, not from the hot path.

### `consumer.py`

Redis consumer group `spec-generator-group` on `stream:execution-paths`. Follows the exact same structure as the embedder and vector-writer consumers:

- `xgroup_create` with `BUSYGROUP` suppression on startup
- `xreadgroup` with `block=1000`, `count=10`
- Per-message: parse → render → `upsert_spec` → `upsert_qdrant` → `xack`
- Malformed messages (`ValidationError`, `KeyError`, `json.JSONDecodeError`): log warning, XACK (discard)
- Transient failures (Postgres/Qdrant unreachable): log error, do NOT XACK (retry on next poll)

### `main.py`

FastAPI app with lifespan. On startup: construct embedding provider, call `store.ensure_collection()`, start consumer background task. On shutdown: cancel task.

Endpoints:
- `GET /health` — always 200, `{"status": "ok", "service": "spec-generator", "version": "0.1.0"}`
- `GET /ready` — pings Redis, Postgres, Qdrant; returns 503 with error list if any fail
- `GET /metrics` — Prometheus text format counters: `messages_processed_total`, `messages_failed_total`, `specs_written_total`, `specs_embedded_total`

---

## Postgres Schema

```sql
CREATE TABLE IF NOT EXISTS behavior_specs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_stable_id  TEXT NOT NULL,
    repo            TEXT NOT NULL,
    commit_sha      TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    spec_text       TEXT NOT NULL,
    branch_coverage FLOAT,
    observed_calls  INTEGER,
    generated_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (node_stable_id, repo, commit_sha)
);
CREATE INDEX ON behavior_specs (node_stable_id, repo);
```

Applied via `store.py` `ensure_schema()` called from lifespan.

---

## Embedding Provider

Reuses the same provider abstraction (`providers/base.py`, `providers/ollama.py`, `providers/voyage.py`) copied from the embedder service. Configured via `EMBEDDING_PROVIDER`, `OLLAMA_URL`, `EMBEDDING_MODEL`, `EMBEDDING_DIM` env vars — identical to embedder's configuration surface.

---

## Testing Strategy

**`test_renderer.py`** — unit tests for `render_spec_text`:
- PATH section renders correct hop order, frequency ratios, avg_ms
- SIDE_EFFECTS section maps all six effect types correctly
- Conditional annotation appears for hop_depth > 1
- CHANGE_IMPACT section aggregates tables and services correctly
- Empty side effects / single-hop path render without errors

**`test_store.py`** — unit tests with mocked asyncpg connection and mocked QdrantClient:
- `upsert_spec` issues correct INSERT with ON CONFLICT clause
- `upsert_qdrant` calls provider.embed once, calls client.upsert with correct payload shape
- `ensure_collection` skips creation if collection already exists (409 suppressed)
- `branch_coverage` computed correctly (all-1.0 → 1.0, mixed → fraction)

**`test_consumer.py`** — unit tests for `_process`:
- Valid message → render called, upsert_spec called, upsert_qdrant called
- `ValidationError` → XACK, no store calls
- Missing `event` key → XACK, no store calls
- Postgres failure → exception propagates (caller does NOT XACK)

---

## Docker / Compose

Dockerfile follows the `python.Dockerfile` base. Added to `docker-compose.yml` with:

```yaml
spec-generator:
  build: ./services/spec-generator
  ports:
    - "8097:8080"
  environment:
    REDIS_URL: redis://redis:6379
    POSTGRES_DSN: postgres://tersecontext:localpassword@postgres:5432/tersecontext
    QDRANT_URL: http://qdrant:6333
    EMBEDDING_PROVIDER: ollama
    OLLAMA_URL: http://ollama:11434
    EMBEDDING_MODEL: nomic-embed-text
    EMBEDDING_DIM: "768"
  depends_on:
    redis:
      condition: service_healthy
    postgres:
      condition: service_healthy
    qdrant:
      condition: service_started
    ollama:
      condition: service_started
```

---

## Definition of Done

- [ ] `GET /health` returns 200
- [ ] Consumer reads from `stream:execution-paths` (consumer group: `spec-generator-group`)
- [ ] `behavior_specs` table created on startup if not exists
- [ ] `specs` Qdrant collection created on startup if not exists
- [ ] Valid ExecutionPath → spec_text rendered → row upserted to Postgres → vector upserted to Qdrant
- [ ] Re-run at same `(node_stable_id, repo, commit_sha)` increments `version`
- [ ] All unit tests pass
- [ ] Dockerfile builds cleanly
- [ ] Service added to `docker-compose.yml`
