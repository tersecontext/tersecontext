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

Consumes `stream:repo-indexed` — a new Redis stream event emitted by repo-watcher. Consumer group: `lsp-indexer-group`. Create with `XGROUP CREATE stream:repo-indexed lsp-indexer-group $ MKSTREAM` on startup; handle `BUSYGROUP` error gracefully (group already exists).

Workspace-level trigger (not file-by-file) because language servers need full project context.

**lsp-indexer must wait for graph-writer to finish writing nodes before running LSP**, otherwise the node resolver lookup returns no matches for newly added files. Ordering is enforced via a Redis marker written by graph-writer after it finishes processing all `stream:parsed-file` events for a commit: `SET graph-writer:repo-ready:{repo}:{commit_sha} 1 EX 3600`. Before starting the LSP session, lsp-indexer polls for this key (1s interval, 60s timeout; proceeds on best-effort if timeout elapses). The `stream:repo-indexed` event payload must therefore include `commit_sha` so lsp-indexer knows which key to wait for: `{"repo": str, "path": str, "commit_sha": str}`. graph-writer requires a small addition: after flushing all nodes for a commit, write the readiness marker to Redis.

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

Only write edges when both nodes already exist (graph-writer owns the node lifecycle — lsp-indexer must not auto-create stub nodes):

```cypher
MATCH (a:Node {stable_id: $src, active: true})
MATCH (b:Node {stable_id: $tgt, active: true})
MERGE (a)-[r:CALLS]->(b)
SET r.source = 'lsp', r.confirmed_at = datetime()
```

If either MATCH returns no rows the edge is silently skipped — the readiness wait (see Trigger section) minimises missed nodes; any that remain unresolved will be picked up on the next scheduled re-index.

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
| `LANGUAGE_SERVERS` | `{"py":["pyright-langserver","--stdio"],"go":["gopls"],"ts":["typescript-language-server","--stdio"]}` | JSON map of ext → server command |
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

- `stream:repo-indexed` — full index of the repo directory. Consumer group: `zoekt-indexer-group`. Create with `XGROUP CREATE stream:repo-indexed zoekt-indexer-group $ MKSTREAM` on startup; handle `BUSYGROUP` gracefully.
- `stream:file-changed` — debounced (100ms window) per-repo re-index of affected shard. Consumer group: `zoekt-file-watcher-group`. Create with `XGROUP CREATE stream:file-changed zoekt-file-watcher-group $ MKSTREAM` on startup; handle `BUSYGROUP` gracefully.

### Indexing Flow

1. On `stream:repo-indexed`: invoke `zoekt-index` via `exec.Command` with explicit string substitution — **do not rely on shell expansion**. Construct the path in Go: `repoPath := filepath.Join(os.Getenv("REPOS_DIR"), repo)`, then `exec.Command("zoekt-index", "-index", indexDir, repoPath)`.
2. On `stream:file-changed`: debounce per repo, then re-index repo shard using the same exec pattern
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

`neo4j.go` is replaced by `zoekt.go`. `retriever.go` is updated at the `buildFullTextQuery` call site and the `GraphSearcher` interface changes. `rrf.go`, `qdrant.go`, and `server.go` are unchanged.

### Updated GraphSearcher Interface

`buildFullTextQuery` is removed along with `neo4j.go`. The `GraphSearcher` interface signature changes to pass keywords and symbols separately so `ZoektSearcher` can apply different query strategies to each:

```go
type GraphSearcher interface {
    Search(ctx context.Context, keywords []string, symbols []string, repo string, limit int) ([]RankedNode, error)
}
```

`retriever.go` is updated to call `r.graph.Search(ctx, intent.Keywords, intent.Symbols, repo, limit)` directly, removing the `buildFullTextQuery` call. The `GraphSearcher` interface is defined in `zoekt.go` (replacing the declaration in `neo4j.go`).

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
4. Resolve to Neo4j `stable_id`s via batched Cypher. For each file+line, multiple nodes may match due to nested scopes (e.g., a method inside a class both have overlapping line ranges). Use the narrowest enclosing node (smallest `end_line - start_line`) as the tiebreaker:
   ```cypher
   UNWIND $matches AS m
   MATCH (n:Node {repo: $repo, file_path: m.file, active: true})
   WHERE n.start_line <= m.line AND n.end_line >= m.line
   WITH n, m, (n.end_line - n.start_line) AS span
   ORDER BY span ASC
   WITH m.line AS line, m.file AS file, m.score AS zoekt_score,
        head(collect(n)) AS n
   RETURN n.stable_id, n.name, n.type, zoekt_score
   ```
5. Deduplicate by `stable_id` across all matches (multiple line hits in same node → take max zoekt_score)
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
    - ZOEKT_URL=http://zoekt-webserver:6070
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
    - 'LANGUAGE_SERVERS={"py":["pyright-langserver","--stdio"],"go":["gopls"],"ts":["typescript-language-server","--stdio"]}'
  depends_on: [redis, neo4j]
  networks: [tersecontext]

volumes:
  zoekt-index:
```

**Removed from docker-compose**: `symbol-resolver`

### New Redis Stream Event

`stream:repo-indexed` — emitted by repo-watcher when a repo clone or update completes. The event payload is `{"repo": str, "path": str, "node_count": int}` where `node_count` is the number of source files in the repo (used by lsp-indexer's readiness wait).

Payload: `{"repo": str, "path": str, "commit_sha": str}`. The `commit_sha` is required by lsp-indexer's readiness wait to construct the Redis key `graph-writer:repo-ready:{repo}:{commit_sha}`.

repo-watcher change required: after completing a clone or pull and emitting the final `stream:file-changed` events for the commit, emit one `XADD stream:repo-indexed * repo {name} path {path} commit_sha {sha}`. This is more than a one-liner — it requires tracking the commit SHA through the watcher loop and determining the right point to fire (after all file-changed events for the commit have been emitted).

Both `lsp-indexer` and `zoekt-indexer` consume it as independent consumer groups (`lsp-indexer-group`, `zoekt-indexer-group`).

### Neo4j Migration

Remove the `CREATE FULLTEXT INDEX node_search` statement from `docker/neo4j-init.cypher` (this is where the index is actually created — not in graph-writer startup).

For existing deployments where the index already exists, `docker/neo4j-init.cypher` does not re-run. Drop the index via a one-time migration script `scripts/migrate_drop_node_search_index.py`:

```python
from neo4j import GraphDatabase
driver = GraphDatabase.driver(NEO4J_URL, auth=(NEO4J_USER, NEO4J_PASSWORD))
with driver.session() as s:
    s.run("DROP INDEX node_search IF EXISTS")
```

Run once against any existing deployment before deploying the new services. The index being absent is harmless on fresh deployments (the `IF EXISTS` guard makes the statement safe to re-run).

### Migration: Existing symbol-resolver Edges

Edges written by symbol-resolver have `source='static'` or no `source` property. These are left in place. graph-enricher's conflict detector already handles the lifecycle: confirmed edges that are never re-observed will eventually downgrade. No purge is required. On the next re-index of each repo, lsp-indexer will write new `source='lsp'` edges for the same cross-file relationships.

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
- [ ] `dual-retriever`: zoekt path replaces Neo4j full-text path, `GraphSearcher` interface updated to `Search(ctx, keywords, symbols []string, repo string, limit int)`
- [ ] `dual-retriever`: zoekt file+line results resolved to Neo4j `stable_id`s correctly
- [ ] `repo-watcher`: emits `stream:repo-indexed` (with `commit_sha`) after all file-changed events for a commit are emitted
- [ ] `graph-writer`: writes `graph-writer:repo-ready:{repo}:{commit_sha}` to Redis after flushing all nodes for a commit
- [ ] `docker/neo4j-init.cypher`: `node_search` fulltext index creation removed
- [ ] `symbol-resolver`: removed from docker-compose
- [ ] `dual-retriever`: `retriever.go` updated to pass keywords/symbols directly to `ZoektSearcher` (no `buildFullTextQuery` call)
- [ ] Migration: existing symbol-resolver edges left in place (no purge needed)
- [ ] Migration: `scripts/migrate_drop_node_search_index.py` run against existing deployments before rollout
- [ ] All new services: `/health`, `/ready`, `/metrics` return 200
- [ ] All new services: Dockerfile builds cleanly
- [ ] All new services: unit tests pass
