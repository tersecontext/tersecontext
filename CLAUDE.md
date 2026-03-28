# CLAUDE.md — Foundation (Phase 0)

<!-- shared context -->
## TerseContext — system overview

TerseContext produces the minimum sufficient context window for an LLM to understand
and safely modify a codebase. It combines static analysis (AST/graph) with dynamic
analysis (runtime traces) and serves both as a single structured plain-text document.

Three pipelines: Static ingestion (Python) → Query pipeline (Go+Python) → Dynamic analysis (Python)
Stores: Neo4j (graph), Qdrant (vectors), Redis (streams+cache), Postgres (specs)

---

## Your role — Foundation

You are building the skeleton that every other service plugs into.
No service can be started until this work is complete and verified.
You own: docker-compose, shared contracts, proto files, Neo4j schema, and the monorepo layout.

You do NOT build any service logic. Your output is infrastructure and schemas only.

---

## What to build

### 1. Monorepo layout

```
tersecontext/
  services/
    parser/
    symbol-resolver/
    embedder/
    graph-writer/
    vector-writer/
    query-understander/
    dual-retriever/
    subgraph-expander/
    serializer/
    api-gateway/
    repo-watcher/
    entrypoint-discoverer/
    instrumenter/
    trace-runner/
    trace-normalizer/
    graph-enricher/
    spec-generator/
  contracts/
    events/
    requests/
    nodes/
  proto/
    query.proto
    ingest.proto
  docker/
    python.Dockerfile
    go.Dockerfile
  docker-compose.yml
  .env.example
  Makefile
  go.work
```

### 2. docker-compose.yml

Services to include:
- neo4j:5     — ports 7474:7474 (UI), 7687:7687 (bolt). Auth: neo4j/localpassword
- qdrant/qdrant — port 6333:6333 (UI+API)
- redis:7     — NO host port mapping (internal only)
- postgres:16 — NO host port mapping. DB=tersecontext, user=tersecontext, pass=localpassword
- ollama/ollama — port 11434:11434 (for model pull access)

All services on a single bridge network named "tersecontext".
Volumes: ./data/neo4j, ./data/qdrant, ./data/redis, ./data/postgres, ./data/ollama

DO NOT add application services to docker-compose yet — those come later.

### 3. Contracts — JSON schemas

Create these files. Use JSON Schema draft-07.

contracts/events/file_changed.json
  repo, commit_sha, path, language, diff_type (added|modified|deleted|full_rescan),
  changed_nodes[], added_nodes[], deleted_nodes[]

contracts/events/parsed_file.json
  file_path, language, repo, commit_sha,
  nodes[]: {stable_id, node_hash, type, name, signature, docstring, body, line_start, line_end, parent_id?}
  intra_file_edges[]: {source_stable_id, target_stable_id, type (CALLS|IMPORTS|DEFINES)}

contracts/events/embedded_nodes.json
  repo, commit_sha,
  nodes[]: {stable_id, vector: number[], embed_text, node_hash}

contracts/events/raw_trace.json
  entrypoint_stable_id, commit_sha, duration_ms,
  events[]: {type (call|return|exception), fn, file, line, timestamp_ms, exc_type?}

contracts/events/execution_path.json
  entrypoint_stable_id, commit_sha,
  call_sequence[]: {stable_id, hop, frequency_ratio, avg_ms}
  side_effects[]: {type (db_read|db_write|cache_read|cache_set|http_out|fs_write), detail, hop_depth}
  dynamic_only_edges[]: {source, target}
  never_observed_static_edges[]: {source, target}
  timing_p50_ms, timing_p99_ms

contracts/requests/query_intent.json
  raw_query, keywords[], symbols[], query_type (lookup|flow|impact), embed_query, scope?

contracts/requests/seed_nodes.json
  nodes[]: {stable_id, name, type, score, retrieval_method (vector|graph|both)}

contracts/requests/ranked_subgraph.json
  nodes[]: {stable_id, name, type, signature, docstring, body?, score, hop, provenance,
            observed_calls?, avg_latency_ms?, branch_coverage?, raises_observed[],
            side_effects[]?, behavior_spec?}
  edges[]: {source, target, type, provenance (static|dynamic|confirmed|conflict), frequency_ratio?}
  warnings[]: {type (CONFLICT|DEAD|STALE), source, target, detail}
  budget_used, total_candidates

contracts/nodes/function_node.json
  stable_id, node_hash, type="function", name, qualified_name, signature, docstring, body,
  file_path, line_start, line_end, language, repo, active, embed_text,
  provenance (static|dynamic|confirmed|conflict), source (static|dynamic|confirmed|conflict),
  observed_calls?, avg_latency_ms?, branch_coverage?

### 4. Proto files

proto/query.proto — define QueryService with methods:
  Understand(UnderstandRequest) → QueryIntentResponse
  Retrieve(RetrieveRequest) → SeedNodesResponse
  Expand(ExpandRequest) → RankedSubgraphResponse
  Serialize(SerializeRequest) → ContextDocResponse

proto/ingest.proto — define IngestService with methods:
  IndexRepo(IndexRequest) → IndexResponse (streaming progress)

Use proto3. Generate Go stubs to services/dual-retriever/gen/ and Python stubs to services/query-understander/gen/

### 5. Neo4j startup migration

Create docker/neo4j-init.cypher:
  CREATE CONSTRAINT stable_id_unique IF NOT EXISTS FOR (n:Node) REQUIRE n.stable_id IS UNIQUE;
  CREATE INDEX node_name IF NOT EXISTS FOR (n:Node) ON (n.name);
  CREATE INDEX node_file IF NOT EXISTS FOR (n:Node) ON (n.file_path);
  CREATE FULLTEXT INDEX node_search IF NOT EXISTS FOR (n:Node) ON EACH [n.name, n.docstring];

Mount this into Neo4j container via docker-compose.

### 6. Makefile targets

make up          — docker-compose up -d
make down        — docker-compose down
make proto       — generate Go + Python stubs from proto/
make verify      — hit /health on all running services
make logs        — docker-compose logs -f

### 7. .env.example

NEO4J_PASSWORD=localpassword
POSTGRES_PASSWORD=localpassword
LLM_MODEL=qwen2.5-coder:7b
VOYAGE_API_KEY=           # optional, leave blank to use local embeddings

---

## Verification — how to confirm this is done correctly

Run these checks. All must pass before this worktree is approved.

```bash
# 1. Stores start cleanly
make up
docker-compose ps  # all 5 containers (neo4j, qdrant, redis, postgres, ollama) should be Up

# 2. Neo4j is reachable
curl http://localhost:7474  # should return HTML (Neo4j browser)
curl -u neo4j:localpassword http://localhost:7474/db/neo4j/tx/commit \
  -H 'Content-Type: application/json' \
  -d '{"statements":[{"statement":"RETURN 1"}]}'
# should return {"results":[{"columns":["1"],"data":[{"row":[1]}]}]}

# 3. Qdrant is reachable
curl http://localhost:6333/healthz  # should return {"title":"qdrant - vector search engine"}

# 4. JSON schemas are valid
pip install jsonschema
python -c "
import json, jsonschema, glob
for f in glob.glob('contracts/**/*.json', recursive=True):
    s = json.load(open(f))
    jsonschema.Draft7Validator.check_schema(s)
    print(f'OK: {f}')
"

# 5. Proto generates without errors
make proto  # no errors, stubs appear in services/*/gen/

# 6. Neo4j constraints applied
curl -u neo4j:localpassword http://localhost:7474/db/neo4j/tx/commit \
  -H 'Content-Type: application/json' \
  -d '{"statements":[{"statement":"SHOW CONSTRAINTS"}]}'
# should list stable_id_unique constraint
```

---

## Definition of done

- [ ] docker-compose up starts all 5 stores cleanly
- [ ] Neo4j browser reachable at localhost:7474
- [ ] Qdrant dashboard reachable at localhost:6333
- [ ] All JSON schemas in contracts/ validate against JSON Schema draft-07
- [ ] make proto generates Go and Python stubs without errors
- [ ] Neo4j UNIQUE constraint on stable_id is present
- [ ] .env.example committed
- [ ] Makefile make up / make down / make proto all work
