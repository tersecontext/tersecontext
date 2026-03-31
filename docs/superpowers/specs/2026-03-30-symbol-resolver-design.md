# Symbol Resolver — Design Spec
**Date:** 2026-03-30
**Status:** Approved

---

## Overview

The symbol-resolver closes the gap between single-file parsing and a connected cross-file knowledge graph. The parser emits import nodes but cannot resolve them to actual nodes in Neo4j (it only sees one file at a time). The symbol-resolver consumes the same `stream:parsed-file` stream, looks up import targets in Neo4j, and writes IMPORTS edges.

Port: 8082 (internal :8080, mapped 8082:8080)
Language: Python
Input: `stream:parsed-file` (consumer group: `symbol-resolver-group`)
Reads: Neo4j (target node lookup)
Writes: Neo4j (IMPORTS edges, Package nodes), Redis (pending_refs queue)

**Out of scope for v1:** Cross-file CALLS resolution. The parser only generates intra-file CALLS edges (targets resolved via a local `name_to_sid` dict). Cross-file CALLS would require the parser to emit unresolved call names — a separate mechanism deferred to a future version. When that work is tackled, the edge model or `ParsedFileEvent` will need extension.

---

## Architecture & Data Flow

```
stream:parsed-file
       │
       ▼  (consumer group: symbol-resolver-group)
  consumer.py
       │
       ├─► resolver.py ──► Neo4j  (IMPORTS edges + Package MERGE)
       │         │
       │         └─► pending.py ──► Redis  (pending_refs:{repo} list)
       │
       └─► pending.py.retry() ──► resolver.py  (retry pending on each new file)
```

### File Structure

```
services/symbol-resolver/
  app/
    main.py          — FastAPI app, lifespan, /health /ready /metrics
    consumer.py      — Redis stream consumer loop
    resolver.py      — import resolution logic, Neo4j queries
    pending.py       — pending_refs queue: push, retry, expire
    neo4j_client.py  — driver factory
    models.py        — ParsedFileEvent, PendingRef
  tests/
    test_resolver.py
  Dockerfile
  requirements.txt
```

---

## Resolution Logic

Only nodes with `type="import"` are processed. All other node types are skipped.

### Symbol extraction

Symbols are extracted from the import node's `body` text using `ast.parse()` (stdlib, no new dependencies). The import node's `name` field holds the module path (e.g. `auth.service`), not the imported symbol — always parse `body` for symbol names.

For aliased imports (`from x import Y as Z`), always look up by `alias.name` (i.e. `Y`), never by `alias.asname` (i.e. `Z`), since Neo4j nodes are indexed by their declared name.

**Known parser limitation:** For multi-module statements (`import os, sys`), the parser emits one import node with `name = "os"` and `body = "import os, sys"`. `ast.parse()` will extract both `os` and `sys` from the body, so both get Package nodes — but both are written from the same `stable_id` (the `os` import node). This is functionally correct for edge writing.

| Body text | Extracted symbols | Action |
|---|---|---|
| `from auth.service import AuthService` | `["AuthService"]` | Neo4j lookup |
| `from auth.service import AuthService, UserService` | `["AuthService", "UserService"]` | Neo4j lookup each |
| `from auth.service import AuthService as AS` | `["AuthService"]` (use `.name`) | Neo4j lookup |
| `from .models import User` | `["User"]` (relative) | Neo4j lookup with path hint |
| `import bcrypt` | `["bcrypt"]` | MERGE Package node |
| `import os` | `["os"]` | MERGE Package node |
| `import os, sys` | `["os", "sys"]` | MERGE Package node for each |
| `import os.path` | `["os.path"]` | MERGE Package node |

**External package detection:** A plain `import X` statement (no `from`) always creates a Package node — no Neo4j lookup attempted. Each distinct name (e.g. `os` and `os.path`) becomes a separate Package node; no parent-child relationship is established between them in v1.

### Neo4j lookup strategy (two attempts)

For `from X import Y` statements:

1. **Exact qualified_name match:**
   ```cypher
   MATCH (n:Node {repo: $repo, qualified_name: $name, active: true})
   RETURN n.stable_id LIMIT 1
   ```

2. **Name + file_path pattern** (module path `auth.service` → path hint `auth/service`):
   ```cypher
   MATCH (n:Node {repo: $repo, name: $name, active: true})
   WHERE n.file_path CONTAINS $path_hint
   RETURN n.stable_id LIMIT 1
   ```

If both fail → push to pending_refs queue.

**Scope of lookup:** These lookups target class nodes and top-level function nodes. Methods (qualified_name `ClassName.method`) are not found by a bare name lookup — if `from x import some_method` refers to a method rather than a top-level function, both lookups will miss and the ref will go to pending_refs. This is expected behavior, not a bug. In practice, Python rarely imports methods directly; typical imports target top-level classes and functions.

### Relative import path hint computation

For relative imports (body starts with `from .` or `from ..`), derive the path hint from the importing file's `file_path`:

- One leading dot (`from .models import X`): strip the filename from `file_path`. E.g. `auth/service.py` → path hint `auth/`.
- Two leading dots (`from ..models import X`): strip the filename and one more directory component. E.g. `auth/views/handler.py` → path hint `auth/`.
- Each additional dot strips one more directory component.

The `file_path` from the `ParsedFileEvent` provides the base for this computation.

**Edge case — root-level relative import:** If the importing file has no parent directory (e.g. `service.py` at the repo root), stripping the filename leaves an empty string `""`. `CONTAINS ""` is always true in Cypher, which degrades the fallback to a repo-wide name search. This is acceptable for v1 and is noted as a known limitation.

### Race condition: importer node may not yet exist in Neo4j

Symbol-resolver consumes from `stream:parsed-file` in parallel with the embedder → graph-writer pipeline. Import nodes are written to Neo4j only after the embedder and graph-writer have processed the `stream:embedded-nodes` event. The importer node may not exist in Neo4j when the IMPORTS edge write fires.

**Handling:** The IMPORTS edge queries use `MATCH` for the importer node. If `MATCH` finds no row, no edge is written — this is the same as a failed target lookup. In this case, the edge is pushed to pending_refs alongside unresolved target lookups, and retried on the next file event for that repo. This means the import node's `stable_id` (the `source_stable_id` in the PendingRef), the target symbol name, and the slash-form path hint are all stored in the pending ref so the retry can reconstruct the full edge.

Note: `source_stable_id` in a `PendingRef` always refers to the import node's `stable_id` (the `type="import"` node from the parsed file), not a file-level node. Import nodes are stored as `Node`-labelled entries in Neo4j and are the correct source for IMPORTS edges.

### Pending ref retry

After processing a new file event, retry all pending_refs for that repo via bulk read-requeue:

1. Read all entries: `LRANGE pending_refs:{repo} 0 -1`
2. Clear the list: `DEL pending_refs:{repo}` (not `LTRIM 0 -1` which is a no-op)
3. For each entry: attempt resolution. If resolved, write the edge. If not, increment `attempt_count`, update `attempted_at`, and RPUSH back unless expired.

Do **not** use BLPOP for retry — it is a blocking single-item dequeue unsuited to bulk read-requeue. BLPOP is only appropriate for a dedicated background worker pattern; this service uses event-triggered retry instead.

**Expiry rule:** Pending refs with `attempt_count > 5` OR older than 24h are logged as unresolved and dropped. The OR condition prevents unbounded queue growth for rarely-retried repos where age alone should be sufficient to declare the import unresolvable. `attempt_count` is incremented before writing back to Redis so that on the next cycle the count already reflects the attempt just made.

---

## Edge Writes to Neo4j

All edges use MERGE for idempotency. All get `source = 'static'`.

```cypher
// Internal import edge
MATCH (importer:Node {stable_id: $importer_id})
MATCH (imported:Node {stable_id: $imported_id})
MERGE (importer)-[r:IMPORTS]->(imported)
SET r.source = 'static', r.updated_at = datetime()

// External package node + edge
MATCH (importer:Node {stable_id: $importer_id})
MERGE (pkg:Package {name: $pkg_name, repo: $repo})
MERGE (importer)-[r:IMPORTS]->(pkg)
SET r.source = 'static', r.updated_at = datetime()
```

If either `MATCH` in the internal import query returns no rows, no edge is written. The caller must detect the zero-row case and push to pending_refs.

Package nodes: `type="package"`, no `embed_text` or vectors.

---

## Data Models

### `PendingRef`
```python
class PendingRef(BaseModel):
    source_stable_id: str   # stable_id of the import node (type="import")
    target_name: str        # imported symbol name (e.g. "AuthService")
    path_hint: str          # slash-separated path for fallback query (e.g. "auth/service")
    edge_type: str          # "IMPORTS"
    repo: str
    attempted_at: datetime
    attempt_count: int = 0
```

`path_hint` is pre-converted to slash-separated form before storage (e.g. module `auth.service` → `auth/service`). Store the converted form, not the raw dotted module path.

Stored as JSON in Redis list `pending_refs:{repo}`. RPUSH to add. Retry via LRANGE + DEL + RPUSH (bulk read-requeue pattern — not BLPOP).

### `ParsedFileEvent`
Reuses the graph-writer model shape with `ConfigDict(extra="ignore")` — unknown fields are silently dropped. Copy the model definition from graph-writer rather than importing across service boundaries.

---

## Docker & Compose

**Dockerfile:** Mirrors graph-writer — `python:3.12-slim`, `uvicorn app.main:app --host 0.0.0.0 --port 8080`.

**docker-compose entry:**
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

Note: no dependency on `graph-writer`. Both services consume `stream:parsed-file` independently and should start as long as their infrastructure (Redis, Neo4j) is healthy.

---

## Tests

All unit tests use a mock Neo4j driver and mock Redis. No live services required.

| # | Test | Covers |
|---|---|---|
| 1 | `test_extract_symbols_from_statement` | `from auth.service import AuthService` → `["AuthService"]` |
| 2 | `test_extract_symbols_multi` | `from x import A, B` → `["A", "B"]` |
| 3 | `test_extract_symbols_aliased` | `from x import Y as Z` → `["Y"]` (use `.name` not `.asname`) |
| 4 | `test_external_package_import` | `import bcrypt` → Package MERGE, no Neo4j lookup |
| 5 | `test_exact_match_writes_imports_edge` | Exact qualified_name hit → IMPORTS edge |
| 6 | `test_path_hint_fallback` | Exact miss, path hint hit → IMPORTS edge |
| 7 | `test_both_lookups_fail_pushes_pending` | Both miss → PendingRef pushed with path_hint |
| 8 | `test_importer_not_yet_in_neo4j_pushes_pending` | MATCH returns no rows → PendingRef pushed |
| 9 | `test_pending_retry_resolves` | New file event → pending retried via LRANGE/DEL/RPUSH, resolved ref cleared |
| 10 | `test_pending_retry_resolves_on_importer_arrival` | Pending ref clears because importer node now exists in Neo4j |
| 11 | `test_pending_expiry_drops_old_ref` | attempt_count > 5 OR age > 24h → dropped |
| 12 | `test_relative_import_path_hint` | `from .models import X` in `auth/service.py` → path hint `auth/` |

---

## Definition of Done

- [ ] /health returns 200
- [ ] IMPORTS edges appear in Neo4j after processing cross-file imports
- [ ] External package imports create Package nodes
- [ ] Pending refs queue accumulates unresolved imports
- [ ] Pending refs resolve when the target file is later indexed
- [ ] All edge writes use MERGE (idempotent)
- [ ] All unit tests pass
- [ ] Dockerfile builds cleanly
