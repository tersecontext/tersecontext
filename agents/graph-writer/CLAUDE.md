# CLAUDE.md — Graph writer

## TerseContext — system overview

TerseContext produces the minimum sufficient context window for an LLM to understand
and safely modify a codebase. Stores: Neo4j (graph), Qdrant (vectors), Redis (streams), Postgres (specs).

Locked stable_id:  sha256(repo + ":" + file_path + ":" + node_type + ":" + qualified_name)
All services expose: GET /health, GET /ready, GET /metrics

---

## Your role — Graph writer

You are one of two consumers of stream:embedded-nodes (the other is vector-writer).
You write nodes and edges to Neo4j. You own the write path to the graph.
You are the ONLY service that writes nodes to Neo4j — no other service does this.

Port: 8084 (internal :8080, mapped 8084:8080)
Language: Python
Input stream:  stream:embedded-nodes  (consumer group: graph-writer-group)
Also reads:    stream:parsed-file     (for edge data and delete events)
Writes to:     Neo4j

---

## Neo4j write rules — CRITICAL

### Node upsert — always MERGE, never CREATE

```cypher
MERGE (n:Node { stable_id: $stable_id })
SET n += {
  name:        $name,
  type:        $type,
  signature:   $signature,
  docstring:   $docstring,
  body:        $body,
  file_path:   $file_path,
  qualified_name: $qualified_name,
  language:    $language,
  repo:        $repo,
  embed_text:  $embed_text,
  node_hash:   $node_hash,
  active:      true,
  updated_at:  datetime()
}
```

### Edge upsert — always MERGE

```cypher
MATCH (a:Node { stable_id: $source }), (b:Node { stable_id: $target })
MERGE (a)-[r:CALLS]->(b)
SET r.source = 'static', r.updated_at = datetime()
```

All edges written by this service get source='static'.
The graph-enricher service later upgrades edges to 'confirmed' or 'conflict'.

### Tombstone on delete

When a FileChanged event has non-empty deleted_nodes:
```cypher
MATCH (n:Node) WHERE n.stable_id IN $stable_ids
SET n.active = false, n.deleted_at = datetime()
WITH n
MATCH (n)-[r]->() DELETE r
WITH n
MATCH ()-[r]->(n) DELETE r
```

Set active=false AND delete edges atomically. Do not hard-delete the node —
BehaviorSpecs may reference it.

---

## Two input streams

This service reads from TWO streams:

1. stream:embedded-nodes — for node upserts (has vectors + embed_text)
2. stream:parsed-file    — for edge upserts and delete events

Process both. Node upserts wait for the embedded-nodes event (which has the
vector data). Edge upserts come from the parsed-file event.

Consume each with its own consumer group:
- stream:embedded-nodes → consumer group "graph-writer-group"
- stream:parsed-file    → consumer group "graph-writer-edges-group"

---

## Service structure

```
services/graph-writer/
  app/
    main.py
    consumer.py       — two consumer loops as background tasks
    writer.py         — all Neo4j write logic
    models.py
  tests/
    test_writer.py
  Dockerfile
  requirements.txt
```

---

## Verification

```bash
# 1. Health
curl http://localhost:8084/health
# expect: {"status":"ok","service":"graph-writer","version":"0.1.0"}

# 2. Index a file and verify nodes appear in Neo4j
# (requires parser + embedder running upstream)
python scripts/push_file_changed.py --path tests/fixtures/sample.py --repo test --diff-type full_rescan
sleep 5

# Query Neo4j
curl -u neo4j:localpassword http://localhost:7474/db/neo4j/tx/commit \
  -H 'Content-Type: application/json' \
  -d '{"statements":[{"statement":"MATCH (n:Node {repo:\"test\"}) RETURN n.name, n.type, n.stable_id LIMIT 10"}]}'
# expect: nodes from sample.py appear with names, types, stable_ids

# 3. Idempotency — run the same file push twice
# Neo4j node count must be identical after both runs (MERGE, not duplicate INSERT)

# 4. Edges — verify CALLS edges appear
curl -u neo4j:localpassword http://localhost:7474/db/neo4j/tx/commit \
  -H 'Content-Type: application/json' \
  -d '{"statements":[{"statement":"MATCH (a)-[r:CALLS]->(b) RETURN a.name, b.name LIMIT 10"}]}'

# 5. Delete — push event with deleted_nodes, verify active=false and edges removed
python scripts/push_file_changed.py --path tests/fixtures/sample.py --repo test --diff-type deleted
sleep 2
# Query: MATCH (n:Node {file_path:"tests/fixtures/sample.py"}) RETURN n.name, n.active
# all should have active=false

# 6. Unit tests
pytest services/graph-writer/tests/ -v
```

---

## Definition of done

- [ ] /health returns 200
- [ ] Nodes appear in Neo4j after processing embedded-nodes events
- [ ] MERGE is idempotent — same file twice = same node count
- [ ] CALLS edges written with source='static'
- [ ] Tombstone sets active=false AND removes edges atomically
- [ ] Consuming from both streams independently
- [ ] All unit tests pass
- [ ] Dockerfile builds cleanly
