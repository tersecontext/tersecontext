# LSP Indexer + Zoekt Search — Design Spec

## Overview

This design replaces the `symbol-resolver` service and the Neo4j full-text index with two new services: an LSP-based cross-reference indexer and a zoekt-backed code search path. The goal is multi-language support and richer cross-reference data (language breadth + semantic graph quality).

**What changes:**

| Component | Before | After |
|---|---|---|
| Cross-file resolution | `symbol-resolver` (Python regex-based) | `lsp-indexer` (LSP call hierarchy) |
| Keyword/code search | Neo4j full-text index (`node_search`) | zoekt trigram index |
| Dual-retriever path 2 | `Neo4jSearcher` full-text | `ZoektSearcher` + node resolver |
| Infrastructure | — | zoekt-webserver container + zoekt-index volume |

**What does not change:**

- Tree-sitter parser (fast structural extraction, intra-file edges)
- Qdrant dense semantic search path (path 1 in dual-retriever)
- RRF merge in dual-retriever
- subgraph-expander (Neo4j graph traversal)
- graph-enricher (dynamic edge enrichment)

---

## New Service: lsp-indexer (port 8098)

### Role

Runs language servers against repos at index time, extracts cross-file call and import edges, writes them to Neo4j with `source='lsp'`. Replaces `symbol-resolver`.

### Language

Python (consistent with other ingestion services).

### Trigger

Consumes `stream:repo-indexed` — a new Redis stream event emitted by repo-watcher when a repo clone or update completes. Workspace-level trigger (not file-by-file) because language servers need full project context.

### Language Server Configuration

Language servers are configured via a `LANGUAGE_SERVERS` env var — a JSON map of file extension to server command:

```json
{
  "py":  ["pyright-langserver", "--stdio"],
  "go":  ["gopls"],
  "ts":  ["typescript-language-server", "--stdio"]
}
```

**Supported now**: Python (`pyright`), Go (`gopls`), TypeScript (`typescript-language-server`).
**Eventual goal**: any language with an LSP server, purely via configuration.

### Indexing Flow (per repo, per detected language)

1. Detect languages present in repo from file extensions
2. For each detected language that has a configured server:
   a. Start language server subprocess via stdio
   b. Send `initialize` + `initialized`
   c. Walk all source files: send `textDocument/didOpen` for each
   d. For each file, call `textDocument/documentSymbol` → get symbol list
   e. For each symbol, call `callHierarchy/prepare` → `callHierarchy/incomingCalls` + `callHierarchy/outgoingCalls`
   f. Map LSP file+position results to Neo4j `stable_id`s via line-range lookup
   g. Write resolved edges to Neo4j
   h. Shutdown language server

### Neo4j Edge Writes

```cypher
MERGE (a:Node {stable_id: $src})
MERGE (b:Node {stable_id: $tgt})
MERGE (a)-[r:CALLS]->(b)
SET r.source = 'lsp', r.confirmed_at = datetime()
```

New `source` value: `'lsp'` — sits alongside existing `'static'`, `'dynamic'`, `'confirmed'`, `'conflict'`. No changes needed to graph-enricher's conflict detector.

### HTTP Endpoints

- `GET /health` — always 200
- `GET /ready` — 200 when Neo4j is reachable
- `GET /metrics` — Prometheus placeholder
- `POST /index` `{"repo": str, "path": str}` — trigger manual re-index

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379` | Redis connection |
| `NEO4J_URL` | `bolt://neo4j:7687` | Neo4j bolt URL |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | (required) | Neo4j password |
| `REPOS_DIR` | (required) | Path to mounted repos |
| `LANGUAGE_SERVERS` | `{}` | JSON map of ext → server command |
| `PORT` | `8098` | HTTP listen port |

### Service Structure

```
services/lsp-indexer/
  app/
    main.py
    indexer.py       # LSP session management + crawl logic
    resolver.py      # file+line → Neo4j stable_id lookup
    neo4j.py         # Neo4j edge writer
    languages.py     # language detection + server config
  tests/
  Dockerfile
  requirements.txt
  pyproject.toml
```

---

## New Service: zoekt-indexer (port 8099)

### Role

Keeps the zoekt corpus in sync with repos on disk. Drives `zoekt-index` to build/update index shards. Does not serve search — that is zoekt-webserver's job.

### Language

Go (consistent with retrieval-path services; zoekt is a Go project).

### Triggers

- `stream:repo-indexed` — full index of the repo directory
- `stream:file-changed` — debounced (100ms window) per-repo re-index of affected shard

### Indexing Flow

1. On `stream:repo-indexed`: run `zoekt-index -index $ZOEKT_INDEX_DIR $REPOS_DIR/{repo}`
2. On `stream:file-changed`: debounce per repo, then re-index repo shard
3. Index shards written to shared `zoekt-index` Docker volume

Repos are indexed with their name as the zoekt repository identifier, so search results carry `repo` in the payload.

### HTTP Endpoints

- `GET /health` — always 200
- `GET /ready` — 200 when zoekt-webserver is reachable
- `GET /metrics` — Prometheus placeholder
- `POST /index` `{"repo": str}` — trigger manual full re-index

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379` | Redis connection |
| `REPOS_DIR` | (required) | Path to mounted repos |
| `ZOEKT_INDEX_DIR` | `/data/index` | Zoekt shard directory |
| `ZOEKT_URL` | `http://zoekt:6070` | Zoekt webserver URL (for readiness check) |
| `PORT` | `8099` | HTTP listen port |

### Service Structure

```
services/zoekt-indexer/
  cmd/
    main.go
  internal/
    indexer/
      indexer.go     # zoekt-index invocation
      debounce.go    # per-repo debounce logic
    server/
      server.go      # HTTP endpoints
  Dockerfile
  go.mod
```

---

## Modified Service: dual-retriever

### What Changes

`neo4j.go` is replaced by `zoekt.go`. All other files (`retriever.go`, `rrf.go`, `qdrant.go`, `server.go`) are unchanged.

### New File: zoekt.go

`ZoektSearcher` implements the existing `GraphSearcher` interface:

```go
type ZoektSearcher struct {
    zoektURL   string
    httpClient *http.Client
    neo4j      neo4j.DriverWithContext  // for file→node resolution only
}
```

**Search flow:**
1. Build zoekt query: symbols get exact-match treatment, keywords get substring search
2. POST to `$ZOEKT_URL/search`:
   ```json
   {"Q": "sym:AuthService OR authenticate", "Opts": {"Repo": "test-repo", "MaxMatchCount": 50}}
   ```
3. Receive file+line matches from zoekt
4. Resolve to Neo4j `stable_id`s via batched Cypher:
   ```cypher
   UNWIND $matches AS m
   MATCH (n:Node {repo: $repo, file_path: m.file, active: true})
   WHERE n.start_line <= m.line AND n.end_line >= m.line
   RETURN n.stable_id, n.name, n.type, m.score AS zoekt_score
   ```
5. Deduplicate by `stable_id` (multiple line hits in same node → take max zoekt_score)
6. Return `[]RankedNode` sorted by zoekt_score descending

Neo4j is retained in the dual-retriever for the file→node resolution lookup only. `NEO4J_USER` and `NEO4J_PASSWORD` env vars remain. The `Neo4jSearcher` struct and `buildFullTextQuery` function are removed.

### New Env Var

`ZOEKT_URL` (default `http://zoekt:6070`)

---

## Infrastructure Changes

### docker-compose additions

```yaml
zoekt-webserver:
  image: sourcegraph/zoekt-webserver:latest
  ports: ["6070:6070"]
  volumes: ["zoekt-index:/data/index"]
  networks: [tersecontext]

zoekt-indexer:
  build: ./services/zoekt-indexer
  ports: ["8099:8099"]
  volumes:
    - "zoekt-index:/data/index"
    - "${REPOS_DIR}:/repos:ro"
  environment:
    - REDIS_URL
    - REPOS_DIR
    - ZOEKT_INDEX_DIR=/data/index
  depends_on: [redis, zoekt-webserver]
  networks: [tersecontext]

lsp-indexer:
  build: ./services/lsp-indexer
  ports: ["8098:8098"]
  volumes:
    - "${REPOS_DIR}:/repos:ro"
  environment:
    - REDIS_URL
    - NEO4J_URL
    - NEO4J_USER
    - NEO4J_PASSWORD
    - REPOS_DIR
    - LANGUAGE_SERVERS
  depends_on: [redis, neo4j]
  networks: [tersecontext]

volumes:
  zoekt-index:
```

**Removed from docker-compose**: `symbol-resolver`

### New Redis Stream Event

`stream:repo-indexed` — emitted by repo-watcher when a repo clone or update completes. repo-watcher requires a one-line addition to emit this event. Both `lsp-indexer` and `zoekt-indexer` consume it as independent consumer groups.

### Neo4j Migration

Remove `CREATE FULLTEXT INDEX node_search` from graph-writer startup. Drop existing index:

```cypher
DROP INDEX node_search IF EXISTS
```

---

## Service Port Registry (updated)

| Service | Port |
|---|---|
| lsp-indexer | 8098 |
| zoekt-indexer | 8099 |
| zoekt-webserver | 6070 |

---

## Definition of Done

- [ ] `lsp-indexer`: on `stream:repo-indexed`, runs LSP against repo, writes `source='lsp'` edges to Neo4j
- [ ] `lsp-indexer`: `LANGUAGE_SERVERS` env var controls which servers run — no hardcoded language list
- [ ] `lsp-indexer`: Python, Go, TypeScript supported
- [ ] `zoekt-indexer`: on `stream:repo-indexed`, indexes repo into zoekt corpus
- [ ] `zoekt-indexer`: on `stream:file-changed`, debounces and re-indexes affected shard
- [ ] `dual-retriever`: zoekt path replaces Neo4j full-text path, `GraphSearcher` interface unchanged
- [ ] `dual-retriever`: zoekt file+line results resolved to Neo4j `stable_id`s correctly
- [ ] `repo-watcher`: emits `stream:repo-indexed` on clone/update completion
- [ ] `graph-writer`: `node_search` fulltext index creation removed
- [ ] `symbol-resolver`: removed from docker-compose
- [ ] All new services: `/health`, `/ready`, `/metrics` return 200
- [ ] All new services: Dockerfile builds cleanly
- [ ] All new services: unit tests pass
