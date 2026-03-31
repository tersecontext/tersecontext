# CLAUDE.md — Embedder service

## TerseContext — system overview

TerseContext produces the minimum sufficient context window for an LLM to understand
and safely modify a codebase. Three pipelines: Static ingestion (Python) → Query pipeline
(Go+Python) → Dynamic analysis (Python). Stores: Neo4j, Qdrant, Redis, Postgres.

Locked stable_id:  sha256(repo + ":" + file_path + ":" + node_type + ":" + qualified_name)
Locked node_hash:  sha256(name + signature + body)
All services expose: GET /health, GET /ready, GET /metrics

---

## Your role — Embedder service

You sit between the parser (upstream) and the graph-writer + vector-writer (downstream).
You take ParsedFile events, build an embed_text for each node, call the embedding model
in batches, and emit EmbeddedNodes events. Both graph-writer and vector-writer consume
your output stream independently.

Port: 8083 (internal :8080, mapped 8083:8080)
Language: Python
Input stream:  stream:parsed-file
Output stream: stream:embedded-nodes
Reads: nothing
Writes: nothing (event emission only)

---

## Embed text formula — CRITICAL, do not change

```python
def build_embed_text(node: dict) -> str:
    parts = [node["name"], node["signature"]]
    if node.get("docstring"):
        parts.append(node["docstring"])
    return " ".join(parts)
```

NEVER include the full body in the embed text. Body degrades embedding quality by
diluting the semantic signal with implementation details.

---

## Embedding model

Provider is configured via env vars:
  EMBEDDING_PROVIDER: "voyage" | "ollama" (default: "ollama")
  VOYAGE_API_KEY: required if provider=voyage
  OLLAMA_URL: default http://ollama:11434
  EMBEDDING_MODEL: default "nomic-embed-text" (ollama) or "voyage-code-3" (voyage)
  EMBEDDING_DIM: 768 (nomic-embed-text) or 1024 (voyage-code-3)

Implement a provider interface:
```python
class EmbeddingProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
```

Implement two classes: VoyageProvider and OllamaProvider.
Load the correct one based on EMBEDDING_PROVIDER at startup.

---

## Batching

Batch nodes in groups of BATCH_SIZE (default 64) before sending to the embedding API.
A single ParsedFile event may contain hundreds of nodes.
Process batches sequentially — do not fire concurrent embedding requests.

---

## Skip unchanged nodes

Each node in ParsedFile carries a node_hash. Before embedding, check if the node's
current node_hash matches what is stored in Neo4j (query by stable_id).
If hashes match: skip embedding for that node (body unchanged).
If hash differs or node is new: embed.

This requires a read from Neo4j. Use the bolt driver. Cache Neo4j node_hash
lookups in a local dict for the duration of processing one ParsedFile event.

---

## Output event

```json
{
  "repo": "acme-api",
  "commit_sha": "a4f91c",
  "nodes": [
    {
      "stable_id": "sha256:...",
      "vector": [0.023, -0.14, ...],
      "embed_text": "authenticate authenticate(user: User, pw: str) -> Token Validates credentials",
      "node_hash": "sha256:..."
    }
  ]
}
```

---

## Service structure

```
services/embedder/
  app/
    main.py
    consumer.py
    embedder.py       — batching + provider dispatch
    providers/
      base.py
      voyage.py
      ollama.py
    models.py
  tests/
    test_embedder.py
  Dockerfile
  requirements.txt
```

---

## Verification

```bash
# 1. Health
curl http://localhost:8083/health
# expect: {"status":"ok","service":"embedder","version":"0.1.0"}

# 2. Push a ParsedFile event and verify EmbeddedNodes output
python scripts/push_parsed_file.py --file tests/fixtures/sample_parsed.json
sleep 3
python scripts/read_stream.py stream:embedded-nodes
# expect: nodes[] with vectors of correct dimension (768 or 1024)
# embed_text must NOT contain the full function body

# 3. Verify batching — push a ParsedFile with 130 nodes
# Embedder should call the embedding API 3 times (64 + 64 + 2), not 130 times

# 4. Verify skip — push same ParsedFile twice
# Second run should embed 0 nodes (all node_hashes already in Neo4j)

# 5. Unit tests
pytest services/embedder/tests/ -v
```

---

## Definition of done

- [ ] /health returns 200
- [ ] Processes ParsedFile and emits EmbeddedNodes with correct vector dimension
- [ ] embed_text does not contain full function body
- [ ] Batching confirmed — 130 nodes = 3 API calls not 130
- [ ] Skip-unchanged confirmed — same file twice = 0 embeddings on second run
- [ ] OllamaProvider and VoyageProvider both implemented and swappable via env
- [ ] All unit tests pass
- [ ] Dockerfile builds cleanly
