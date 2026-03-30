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

**Out of scope for v1:** Cross-file CALLS resolution. The parser only generates intra-file CALLS edges; cross-file CALLS requires a separate mechanism and is deferred.

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

Symbols are extracted from the import node's `body` text using `ast.parse()` (stdlib, no new dependencies).

| Body text | Extracted symbols | Action |
|---|---|---|
| `from auth.service import AuthService` | `["AuthService"]` | Neo4j lookup |
| `from auth.service import AuthService, UserService` | `["AuthService", "UserService"]` | Neo4j lookup each |
| `from .models import User` | `["User"]` (relative) | Neo4j lookup with path hint |
| `import bcrypt` | `["bcrypt"]` | MERGE Package node |
| `import os` | `["os"]` | MERGE Package node |
| `import os.path` | `["os.path"]` | MERGE Package node |

**External package detection:** A plain `import X` statement (no `from`) always creates a Package node — no Neo4j lookup attempted.

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

### Pending ref retry

After resolving imports from a new file event, retry all pending_refs for that repo. New nodes may have just satisfied old refs. On each retry attempt: `attempt_count` increments and `attempted_at` updates.

**Expiry rule:** Pending refs older than 24h AND with >5 attempts are logged as unresolved and dropped. These are likely external packages or typos.

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

Package nodes: `type="package"`, no `embed_text` or vectors.

---

## Data Models

### `PendingRef`
```python
class PendingRef(BaseModel):
    source_stable_id: str
    target_name: str
    edge_type: str          # "IMPORTS"
    repo: str
    attempted_at: datetime
    attempt_count: int = 0
```

Stored as JSON in Redis list `pending_refs:{repo}`. RPUSH to add, BLPOP to consume during retry.

### `ParsedFileEvent`
Reuses the same shape as graph-writer's model with `extra="ignore"`.

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
    graph-writer:
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

## Tests

All unit tests use a mock Neo4j driver. No live services required.

| # | Test | Covers |
|---|---|---|
| 1 | `test_extract_symbols_from_statement` | `from auth.service import AuthService` → `["AuthService"]` |
| 2 | `test_extract_symbols_multi` | `from x import A, B` → `["A", "B"]` |
| 3 | `test_external_package_import` | `import bcrypt` → Package MERGE, no Neo4j lookup |
| 4 | `test_exact_match_writes_imports_edge` | Exact qualified_name hit → IMPORTS edge |
| 5 | `test_path_hint_fallback` | Exact miss, path hint hit → IMPORTS edge |
| 6 | `test_both_lookups_fail_pushes_pending` | Both miss → PendingRef pushed |
| 7 | `test_pending_retry_resolves` | New file event → pending retried, resolved ref cleared |
| 8 | `test_pending_expiry_drops_old_ref` | >24h + >5 attempts → dropped, not retried |

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
