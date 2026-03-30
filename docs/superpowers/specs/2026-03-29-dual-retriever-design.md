# Dual Retriever — Design Spec

## Overview

The dual-retriever is the second service in the TerseContext query pipeline. It receives a `RetrieveRequest` (containing a `QueryIntentResponse`, repo, and max_seeds) via gRPC and returns a ranked list of seed nodes. It fans out to both Qdrant (semantic search) and Neo4j (keyword/symbol search) in parallel, then merges results with Reciprocal Rank Fusion.

- **Port**: 8087 (gRPC)
- **Language**: Go 1.23
- **Reads**: Qdrant, Neo4j, embedder (HTTP)
- **Writes**: nothing

## Proto Changes

Update `RetrieveRequest` in `proto/query.proto` to add `repo` and `max_seeds` fields:

```protobuf
message RetrieveRequest {
  QueryIntentResponse intent = 1;
  string repo = 2;
  int32 max_seeds = 3;
}
```

Regenerate stubs with `make proto`.

**Note:** The proto uses `QueryIntentResponse` (not `QueryIntent` as CLAUDE.md describes) and `double score` (not `float`). The proto file is authoritative. This change also affects the API gateway which sends `RetrieveRequest` — it will need to populate the new fields. Since the API gateway is not yet implemented, there is no backward-compatibility concern.

## Service Structure

```
services/dual-retriever/
  cmd/
    main.go
  internal/
    retriever/
      retriever.go
      qdrant.go
      neo4j.go
      rrf.go
    server/
      server.go
  gen/                 # generated proto stubs
  go.mod
  Dockerfile
```

## Entry Point (main.go)

1. Read env vars: `NEO4J_URL`, `NEO4J_USER`, `NEO4J_PASSWORD`, `QDRANT_URL`, `EMBEDDER_URL`, `PORT` (default 8087)
2. Initialize Neo4j driver, Qdrant client, HTTP client for embedder
3. Construct `Retriever` with both clients
4. Start gRPC server with `QueryService` + `grpc.health.v1.Health`
5. Graceful shutdown on SIGINT/SIGTERM

## Interfaces

```go
type Embedder interface {
    Embed(ctx context.Context, text string) ([]float32, error)
}

type VectorSearcher interface {
    Search(ctx context.Context, vector []float32, repo string, limit int) ([]RankedNode, error)
}

type GraphSearcher interface {
    Search(ctx context.Context, query string, symbols []string, repo string, limit int) ([]RankedNode, error)
}
```

`Embedder` is a separate interface implemented in `qdrant.go` (same file, since it's only used by the vector path). The `Retriever` orchestrator calls `Embed` then passes the vector to `VectorSearcher.Search`.

## Internal Types

```go
type RankedNode struct {
    StableID string
    Name     string
    Type     string
    Source   string // "vector" or "graph"
}
```

Score is not carried from the source — RRF computes its own scores purely from rank position.

## Retrieval Path 1: Qdrant Semantic Search (qdrant.go)

The orchestrator calls `Embedder.Embed(ctx, intent.embed_query)` to get the vector, then passes it to `VectorSearcher.Search`. If `embed_query` is empty, the vector path is skipped entirely (no embedder call, no Qdrant search).

1. `Embedder.Embed`: `POST EMBEDDER_URL/embed` with `{"text": text}` → get `[]float32` vector. Non-200 or unreachable → return error (caught by orchestrator timeout/graceful degradation).
2. `VectorSearcher.Search`: Search Qdrant `nodes` collection with that vector, `limit = max_seeds * 2`, filter `repo` in payload, `with_payload: true`
3. Return `[]RankedNode` sorted by Qdrant score descending

Both `Embedder` and `VectorSearcher` implementations live in `qdrant.go`.

## Retrieval Path 2: Neo4j Keyword + Symbol Search (neo4j.go)

1. Build full-text query from intent: join symbols + keywords with `OR` (e.g. `"authenticate OR AuthService OR auth login jwt"`). If both `keywords` and `symbols` are empty, skip the Neo4j path entirely
2. Run full-text index query:
   ```cypher
   CALL db.index.fulltext.queryNodes("node_search", $query)
   YIELD node, score
   WHERE node.repo = $repo AND node.active = true
   RETURN node.stable_id, node.name, node.type, score
   ORDER BY score DESC
   LIMIT $limit
   ```
3. Run direct name match:
   ```cypher
   MATCH (n:Node {repo: $repo, active: true})
   WHERE n.name IN $symbols OR n.qualified_name IN $symbols
   RETURN n.stable_id, n.name, n.type
   ```
4. Deduplicate — direct match nodes get prepended (they're exact hits)
5. Return `[]RankedNode`

## Parallel Orchestration (retriever.go)

- Fan out both paths as goroutines via channels
- 500ms timeout — collect whatever results are available
- Pass both result lists to RRF
- If both fail/empty, return empty `SeedNodesResponse` (no error — graceful degradation)

```go
vectorResults := make(chan []RankedNode, 1)
graphResults  := make(chan []RankedNode, 1)

go func() { vectorResults <- embed+searchQdrant(ctx, intent) }()
go func() { graphResults  <- searchNeo4j(ctx, intent) }()

// Wait for both or timeout at 500ms
timer := time.NewTimer(500 * time.Millisecond)
defer timer.Stop()
collected := 0
for collected < 2 {
    select {
    case <-timer.C:
        log.Warn("retrieval timeout — using partial results")
        goto drain
    case ...: // receive from either channel
        collected++
    }
}
drain:

// Non-blocking drain of whatever arrived
var vr, gr []RankedNode
select { case vr = <-vectorResults: default: }
select { case gr = <-graphResults:  default: }
```

The 500ms timeout covers the entire vector path (embed + search). Both goroutines write to buffered channels so they never block. After timeout, non-blocking reads drain whatever is available. Goroutines that finish late write to the buffered channel and exit cleanly.

Log per-path latency for observability (see Observability section).

## Reciprocal Rank Fusion (rrf.go)

Pure function:

```go
func RRF(lists [][]RankedNode, k int, maxSeeds int) []SeedNode
```

- `k = 60` (constant, not configurable)
- For each list, each node at rank `r` gets score `1.0 / (k + r + 1)`
- Accumulate scores per `stable_id` across lists
- Track which sources contributed → `retrieval_method`: `"both"`, `"vector"`, or `"graph"`
- Sort by accumulated score descending, return top `max_seeds`
- `name` and `type` carried through from `RankedNode` data

## gRPC Server (server.go)

- Implements `QueryService/Retrieve` only
- Returns `codes.Unimplemented` for `Understand`, `Expand`, `Serialize`
- `Retrieve` handler: extract intent/repo/max_seeds, default max_seeds to 8 if 0 (named constant `DefaultMaxSeeds`), call retriever, map to `SeedNodesResponse`
- Registers `grpc.health.v1.Health` — reports `SERVING` once clients are initialized

## HTTP Health Server (main.go)

A minimal HTTP server on `PORT+1` (default 8088) exposes the platform-required endpoints:

- `GET /health` — always returns 200 `{"status":"ok"}`
- `GET /ready` — returns 200 if Neo4j and Qdrant clients are initialized, 503 otherwise
- `GET /metrics` — placeholder, returns 200 with empty body (Prometheus integration deferred)

This is a simple `net/http` server started alongside the gRPC server — no gRPC-gateway needed.

## Observability

Structured logging (`log/slog`) with:
- Per-path latency: `vector_ms` and `graph_ms` fields on each Retrieve call
- Result counts: `vector_count` and `graph_count`
- Timeout indicator: `timeout=true` when 500ms exceeded
- Total RPC latency: `retrieve_ms`

## Dockerfile

Multi-stage build:
- Builder: `golang:1.23-alpine`, compile binary
- Runtime: `alpine`, copy binary, expose 8087 and 8088

## docker-compose Addition

Add `dual-retriever` service on port 8087, depends on neo4j, qdrant, embedder. Network: `tersecontext`.

## Testing Strategy

- **RRF**: Pure unit tests — multiple lists, single list, empty lists, deduplication, max_seeds cap
- **Retriever orchestration**: Mock `VectorSearcher` and `GraphSearcher` — test parallel execution, timeout (one mock sleeps > 500ms), both fail gracefully
- **Server**: Integration-style test with mock retriever — verify gRPC request/response mapping, default max_seeds

No tests against real Neo4j/Qdrant — covered by CLAUDE.md verification steps.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8087` | gRPC listen port |
| `NEO4J_URL` | `bolt://neo4j:7687` | Neo4j bolt URL |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | (required) | Neo4j password |
| `QDRANT_URL` | `http://qdrant:6333` | Qdrant HTTP URL |
| `EMBEDDER_URL` | `http://embedder:8080` | Embedder HTTP URL |

## Definition of Done

- [ ] Proto updated with `repo` and `max_seeds`, stubs regenerated
- [ ] gRPC server starts and responds to Retrieve calls
- [ ] Both Qdrant and Neo4j queried concurrently (verify with timing logs)
- [ ] RRF: node in both results ranks higher than node in one result
- [ ] `retrieval_method` correctly set to `"vector"`, `"graph"`, or `"both"`
- [ ] Graceful degradation: one store down → partial results, no crash
- [ ] gRPC health check responds
- [ ] All Go tests pass
- [ ] Dockerfile builds cleanly
- [ ] HTTP /health, /ready, /metrics endpoints respond on port 8088
- [ ] docker-compose updated
