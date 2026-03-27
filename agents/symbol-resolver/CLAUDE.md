# CLAUDE.md — Symbol resolver

## TerseContext — system overview

TerseContext produces minimum sufficient LLM context. Symbol resolver closes the gap
between single-file parsing and a fully connected cross-file knowledge graph.

All services expose: GET /health, GET /ready, GET /metrics

---

## Your role — Symbol resolver

The parser only sees one file at a time. You see the whole graph. When the parser
encounters `from auth.service import AuthService`, it emits an unresolved import.
You resolve it by finding the AuthService node in Neo4j and writing the IMPORTS edge.

Without you, the graph has nodes but almost no cross-file edges — useless for
understanding how the codebase connects.

Port: 8082 (internal :8080, mapped 8082:8080)
Language: Python
Input stream:  stream:parsed-file  (consumer group: symbol-resolver-group)
Reads:         Neo4j (target node lookup)
Writes:        Neo4j (resolved edges), Redis (pending_refs queue)

Build this AFTER graph-writer is stable and the query pipeline is working end-to-end.

---

## Two types of resolution

### 1. Import resolution

For each Import/ImportFrom node in ParsedFile.nodes:
```
from auth.service import AuthService
```
→ Look up in Neo4j: MATCH (n:Node {repo: $repo, qualified_name: "AuthService", active: true})
→ If found: write IMPORTS edge from the importing file node to AuthService
→ If not found: push to pending_refs queue

```
import bcrypt
```
→ External package: write IMPORTS edge from file node to a Package node
   MERGE (p:Package {name: "bcrypt", repo: $repo})
   Package nodes have type="package", never have embed_text or vectors

### 2. Cross-file call resolution

For each CALLS edge in intra_file_edges where the target is not in the same file:
→ Look up target by name in Neo4j within the same repo
→ If found: write CALLS edge with source='static'
→ If not found: push to pending_refs

---

## Pending refs queue

Key: "pending_refs:{repo}"
Type: Redis List (RPUSH to add, BLPOP to consume)

Each pending ref:
```json
{
  "source_stable_id": "sha256:...",
  "target_name": "AuthService",
  "edge_type": "IMPORTS",
  "repo": "acme-api",
  "attempted_at": "2024-01-01T00:00:00Z"
}
```

On each new ParsedFile event: after resolving new nodes, retry all pending_refs for
that repo. New nodes may have just been written that satisfy old pending refs.

Pending refs older than 24 hours with > 5 retry attempts: log as unresolved and drop.
These are likely external packages or typos in the source code.

---

## Edge writes to Neo4j

All edges written by symbol-resolver get source='static' (same as graph-writer).
Use MERGE to avoid duplicates.

```cypher
// Import edge
MATCH (importer:Node {stable_id: $importer_id})
MATCH (imported:Node {stable_id: $imported_id})
MERGE (importer)-[r:IMPORTS]->(imported)
SET r.source = 'static', r.updated_at = datetime()

// External package edge
MATCH (importer:Node {stable_id: $importer_id})
MERGE (pkg:Package {name: $pkg_name, repo: $repo})
MERGE (importer)-[r:IMPORTS]->(pkg)
SET r.source = 'static'
```

---

## Language-specific import resolution — Python only in v1

Python module path resolution:
- `from auth.service import AuthService` → qualified_name = "AuthService", file_path contains "auth/service"
- `from .models import User` → relative import, resolve relative to current file's directory
- `import os` → external package, create Package node

Resolution strategy:
1. Try exact qualified_name match in Neo4j
2. Try name match filtered by file_path pattern (module path → directory path)
3. If both fail: push to pending_refs

---

## Service structure

```
services/symbol-resolver/
  app/
    main.py
    consumer.py
    resolver.py       — main resolution logic
    pending.py        — pending_refs queue management
    neo4j_client.py
    models.py
  tests/
    test_resolver.py
  Dockerfile
  requirements.txt
```

---

## Verification

```bash
# Prerequisite: graph-writer has indexed a multi-file Python project

# 1. Health
curl http://localhost:8082/health

# 2. Index a project with cross-file imports
# e.g. a file that does: from auth.service import AuthService
python scripts/push_file_changed.py --path tests/fixtures/views.py --repo test --diff-type full_rescan
sleep 5

# 3. Verify IMPORTS edge appears in Neo4j
curl -u neo4j:localpassword http://localhost:7474/db/neo4j/tx/commit \
  -H 'Content-Type: application/json' \
  -d '{"statements":[{"statement":"MATCH (a)-[r:IMPORTS]->(b) WHERE a.repo=\"test\" RETURN a.name, b.name, r.source LIMIT 10"}]}'
# expect: edges from importing file to AuthService (and other imported symbols)
# all r.source = "static"

# 4. Pending refs — deliberately push a file that imports a not-yet-indexed module
# Check Redis: LLEN pending_refs:test should be > 0
redis-cli LLEN pending_refs:test

# 5. Push the missing module — pending refs should resolve
python scripts/push_file_changed.py --path tests/fixtures/auth_service.py --repo test --diff-type full_rescan
sleep 5
redis-cli LLEN pending_refs:test  # should be 0 (or lower)

# 6. Unit tests
pytest services/symbol-resolver/tests/ -v
```

---

## Definition of done

- [ ] /health returns 200
- [ ] IMPORTS edges appear in Neo4j after processing cross-file imports
- [ ] External package imports create Package nodes
- [ ] Pending refs queue accumulates unresolved imports
- [ ] Pending refs resolve when the target file is later indexed
- [ ] All edge writes use MERGE (idempotent)
- [ ] All unit tests pass
- [ ] Dockerfile builds cleanly
