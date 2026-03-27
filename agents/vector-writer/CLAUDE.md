# CLAUDE.md — Vector writer

## TerseContext — system overview

TerseContext produces the minimum sufficient context window for an LLM to understand
and safely modify a codebase. Stores: Neo4j (graph), Qdrant (vectors), Redis (streams).

All services expose: GET /health, GET /ready, GET /metrics

---

## Your role — Vector writer

You are the second consumer of stream:embedded-nodes (the other is graph-writer).
You write vectors to Qdrant. You are the ONLY service that writes to the nodes
collection in Qdrant.

You run entirely in parallel with graph-writer — same input, different output store.
You do not know graph-writer exists and it does not know you exist.

Port: 8085 (internal :8080, mapped 8085:8080)
Language: Python
Input stream:  stream:embedded-nodes  (consumer group: vector-writer-group)
Also reads:    stream:file-changed    (for delete events)
Writes to:     Qdrant nodes collection

---

## Qdrant collection spec

Collection name: "nodes"
Created on startup if it does not exist.
Vector size: EMBEDDING_DIM env var (default 768 for nomic-embed-text, 1024 for voyage-code-3)
Distance: Cosine

Point structure:
```json
{
  "id": "<stable_id as UUID or string>",
  "vector": [0.023, ...],
  "payload": {
    "name": "authenticate",
    "type": "function",
    "file_path": "auth/service.py",
    "language": "python",
    "repo": "acme-api",
    "node_hash": "sha256:..."
  }
}
```

Payload is intentionally minimal — full node properties live in Neo4j.
The dual-retriever uses stable_id from Qdrant to fetch full data from Neo4j.

Note: Qdrant point IDs must be either UUIDs or unsigned integers.
Use a deterministic UUID from the stable_id: uuid.uuid5(uuid.NAMESPACE_OID, stable_id)

---

## Delete handling

Listen to stream:file-changed for events where deleted_nodes is non-empty.
Delete those points from Qdrant by their derived UUID.

```python
for stable_id in event["deleted_nodes"]:
    point_id = str(uuid.uuid5(uuid.NAMESPACE_OID, stable_id))
    qdrant_client.delete(collection_name="nodes", points_selector=[point_id])
```

---

## Service structure

```
services/vector-writer/
  app/
    main.py
    consumer.py       — two consumer loops (embedded-nodes + file-changed)
    writer.py         — Qdrant upsert + delete logic
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
curl http://localhost:8085/health
# expect: {"status":"ok","service":"vector-writer","version":"0.1.0"}

# 2. Collection created on startup
curl http://localhost:6333/collections/nodes
# expect: collection info with correct vector_size

# 3. Index a file and verify vectors appear
python scripts/push_file_changed.py --path tests/fixtures/sample.py --repo test --diff-type full_rescan
sleep 5

curl -X POST http://localhost:6333/collections/nodes/points/scroll \
  -H 'Content-Type: application/json' \
  -d '{"limit":5,"with_payload":true,"with_vector":false}'
# expect: points with payload {name, type, file_path, repo}

# 4. Semantic search works
curl -X POST http://localhost:6333/collections/nodes/points/search \
  -H 'Content-Type: application/json' \
  -d "{\"vector\": $(python scripts/embed_text.py 'authentication login jwt'), \"limit\": 5, \"with_payload\": true}"
# expect: top results include auth-related functions

# 5. Delete — push deleted event, verify points removed
python scripts/push_file_changed.py --path tests/fixtures/sample.py --repo test --diff-type deleted
sleep 2
# Re-run scroll — points for that file should be gone

# 6. Idempotency — upsert same vectors twice = same collection state

# 7. Unit tests
pytest services/vector-writer/tests/ -v
```

---

## Definition of done

- [ ] /health returns 200
- [ ] nodes collection created on startup with correct vector_size
- [ ] Vectors appear in Qdrant after processing events
- [ ] Semantic search returns relevant results
- [ ] Delete removes points from Qdrant
- [ ] Idempotent — same embeddings twice = same collection state
- [ ] All unit tests pass
- [ ] Dockerfile builds cleanly
