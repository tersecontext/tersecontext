# CLAUDE.md — Dual retriever

## TerseContext — system overview

TerseContext produces the minimum sufficient context window for an LLM to understand
and safely modify a codebase. Three pipelines: Static ingestion (Python) → Query pipeline
(Go+Python) → Dynamic analysis (Python). Stores: Neo4j, Qdrant, Redis, Postgres.

All services expose: GET /health, GET /ready, GET /metrics

---

## Your role — Dual retriever

You are the second service in the query pipeline. You receive a QueryIntent from the
API gateway and return a ranked list of seed nodes. You fan out to both Qdrant (semantic
search) and Neo4j (keyword/symbol search) in parallel and merge results with
Reciprocal Rank Fusion.

Port: 8087 (gRPC)
Language: Go
Input:  gRPC RetrieveRequest (QueryIntent + repo + options)
Output: gRPC SeedNodesResponse
Reads:  Qdrant (semantic search), Neo4j (keyword + symbol search)
Writes: nothing

---

## gRPC interface (from proto/query.proto)

```protobuf
service QueryService {
  rpc Retrieve(RetrieveRequest) returns (SeedNodesResponse);
}

message RetrieveRequest {
  QueryIntent intent = 1;
  string repo = 2;
  int32 max_seeds = 3; // default 8
}

message SeedNodesResponse {
  repeated SeedNode nodes = 1;
}

message SeedNode {
  string stable_id = 1;
  string name = 2;
  string type = 3;
  float score = 4;
  string retrieval_method = 5; // "vector" | "graph" | "both"
}
```

---

## Two retrieval paths — run as goroutines

### Path 1: Qdrant semantic search

1. The QueryIntent includes embed_query (a string). You must embed it first.
   Call the embedder's HTTP endpoint: POST /embed { text: string } → { vector: float[] }
   The embedder exposes this endpoint for on-demand embedding (not just stream processing).
   EMBEDDER_URL env var, default http://embedder:8080

2. Search Qdrant nodes collection:
   - vector: the embed_query vector
   - limit: max_seeds * 2 (fetch extra, RRF will trim)
   - filter by repo in payload
   - return with_payload: true

3. Return list of (stable_id, rank) sorted by Qdrant score descending.

### Path 2: Neo4j keyword + symbol search

Use the full-text index created in Foundation:
```cypher
CALL db.index.fulltext.queryNodes("node_search", $query)
YIELD node, score
WHERE node.repo = $repo AND node.active = true
RETURN node.stable_id, node.name, node.type, score
ORDER BY score DESC
LIMIT $limit
```

Build the full-text query from QueryIntent:
- If symbols is non-empty: search for exact symbol names first
- Add keywords as additional terms
- Query: "authenticate OR AuthService OR auth login jwt"

Also run a direct name match for exact symbols:
```cypher
MATCH (n:Node {repo: $repo, active: true})
WHERE n.name IN $symbols OR n.qualified_name IN $symbols
RETURN n.stable_id, n.name, n.type
```

### Parallel execution

```go
var wg sync.WaitGroup
vectorResults := make(chan []RankedNode, 1)
graphResults  := make(chan []RankedNode, 1)

wg.Add(2)
go func() { defer wg.Done(); vectorResults <- searchQdrant(ctx, intent) }()
go func() { defer wg.Done(); graphResults  <- searchNeo4j(ctx, intent) }()

// Timeout after 500ms — return partial if one store is slow
done := make(chan struct{})
go func() { wg.Wait(); close(done) }()
select {
case <-done:
case <-time.After(500 * time.Millisecond):
    log.Warn("retrieval timeout — using partial results")
}
```

---

## Reciprocal Rank Fusion

```go
func rrf(lists [][]RankedNode, k int) []SeedNode {
    scores := map[string]float64{}
    methods := map[string][]string{}
    for _, list := range lists {
        for rank, node := range list {
            scores[node.StableID] += 1.0 / float64(k + rank + 1)
            methods[node.StableID] = append(methods[node.StableID], node.Source)
        }
    }
    // sort by score desc, return top max_seeds
}
```

k = 60 (standard constant, do not change)

retrieval_method on each SeedNode:
- "both"   if stable_id appeared in both lists
- "vector" if only in Qdrant results
- "graph"  if only in Neo4j results

---

## Service structure

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
      server.go       — gRPC server implementation
  gen/                — generated proto stubs (do not edit)
  go.mod
  Dockerfile
```

---

## Verification

```bash
# 1. Service starts
cd services/dual-retriever
docker build -t tersecontext-dual-retriever .
docker run -d --name retriever-test \
  -e NEO4J_URL=bolt://host.docker.internal:7687 \
  -e NEO4J_PASSWORD=localpassword \
  -e QDRANT_URL=http://host.docker.internal:6333 \
  -e EMBEDDER_URL=http://host.docker.internal:8083 \
  -p 8087:8087 tersecontext-dual-retriever

# Test via grpcurl (install: brew install grpcurl)
grpcurl -plaintext \
  -d '{
    "intent": {
      "raw_query": "how does authentication work",
      "keywords": ["auth","authenticate","login"],
      "symbols": ["authenticate","AuthService"],
      "query_type": "flow",
      "embed_query": "authentication login flow jwt token"
    },
    "repo": "test-repo",
    "max_seeds": 8
  }' \
  localhost:8087 QueryService/Retrieve
# expect: SeedNodesResponse with nodes[], authenticate should rank highly
# expect: retrieval_method="both" for nodes found in both stores

# 2. Health via gRPC health protocol
grpcurl -plaintext localhost:8087 grpc.health.v1.Health/Check

# 3. Timeout test — stop Qdrant, verify partial results still returned
docker stop qdrant
grpcurl -plaintext -d '{"intent":{"keywords":["auth"]},"repo":"test"}' \
  localhost:8087 QueryService/Retrieve
# expect: results returned from Neo4j only, no error

# 4. Unit tests
cd services/dual-retriever && go test ./...
```

---

## Definition of done

- [ ] gRPC server starts and responds to Retrieve calls
- [ ] authenticate/AuthService returned as top seeds for auth-related queries
- [ ] Both Qdrant and Neo4j queried concurrently (verify with timing logs)
- [ ] RRF: node in both results ranks higher than node in one result
- [ ] retrieval_method correctly set to "vector", "graph", or "both"
- [ ] Graceful degradation: one store down → partial results, no crash
- [ ] gRPC health check responds
- [ ] All Go tests pass
- [ ] Dockerfile builds cleanly
