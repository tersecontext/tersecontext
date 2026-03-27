# CLAUDE.md — Subgraph expander

## TerseContext — system overview

TerseContext produces minimum sufficient LLM context. Query pipeline: API gateway →
Query understander → Dual retriever → Subgraph expander → Serializer.

All services expose: GET /health, GET /ready, GET /metrics

---

## Your role — Subgraph expander

You receive seed nodes from the dual retriever and expand outward through the knowledge
graph via BFS traversal. You score every discovered node, prune to a token budget,
and return a RankedSubgraph that the serializer renders into the context document.

This is the most algorithmically complex service in the query pipeline.

Port: 8088 (gRPC)
Language: Go
Input:  gRPC ExpandRequest (SeedNodes + options)
Output: gRPC RankedSubgraphResponse
Reads:  Neo4j (graph traversal), Postgres (BehaviorSpec existence check)
Writes: nothing

---

## gRPC interface

```protobuf
rpc Expand(ExpandRequest) returns (RankedSubgraphResponse);

message ExpandRequest {
  repeated SeedNode seeds = 1;
  string query_type = 2;     // "lookup" | "flow" | "impact"
  int32 max_tokens = 3;      // default 2000
  int32 hop_depth = 4;       // default 2
  float decay = 5;           // default 0.7
}
```

---

## BFS traversal — application-side, NOT a single Cypher query

Do NOT write a single Cypher query with variable-length paths.
Do application-side BFS with one Neo4j query per hop. This gives you control
over per-hop scoring and visited-set management.

```go
type BFSState struct {
    StableID  string
    Score     float64
    Hop       int
    ParentID  string
    EdgeType  string
    Provenance string
}

visited := map[string]BFSState{}
queue   := []BFSState{} // seed nodes at hop 0 with score 1.0

for hop := 0; hop <= hopDepth; hop++ {
    currentLevel := nodesAtHop(queue, hop)
    neighbors := batchFetchNeighbors(ctx, currentLevel, queryType)
    for _, n := range neighbors {
        if existing, seen := visited[n.StableID]; seen {
            // keep higher score version only
            continue
        }
        n.Score = parentScore * decay * edgeTypeWeight(n.EdgeType) * provenanceWeight(n.Provenance)
        visited[n.StableID] = n
        queue = append(queue, n)
    }
}
```

---

## Edge types to follow per query_type

```
flow:   CALLS (forward), IMPORTS, INHERITS
lookup: DEFINES, IMPORTS, INHERITS
impact: CALLS (REVERSE — find callers), TESTED_BY
```

Neo4j query for each hop (flow example):
```cypher
MATCH (seed:Node)-[r:CALLS|IMPORTS|INHERITS]->(neighbor:Node)
WHERE seed.stable_id IN $stable_ids
  AND neighbor.active = true
  AND neighbor.repo = $repo
RETURN seed.stable_id AS parent_id,
       neighbor.stable_id, neighbor.name, neighbor.type,
       neighbor.signature, neighbor.docstring, neighbor.body,
       type(r) AS edge_type,
       r.source AS provenance,
       r.frequency_ratio AS frequency_ratio
```

For impact queries, reverse the direction:
```cypher
MATCH (caller:Node)-[r:CALLS]->(seed:Node)
WHERE seed.stable_id IN $stable_ids ...
```

---

## Scoring weights — DO NOT change these values

```go
var edgeTypeWeights = map[string]float64{
    "CALLS":       1.0,
    "IMPORTS":     0.8,
    "INHERITS":    0.7,
    "MODIFIED_BY": 0.6,
    "TESTED_BY":   0.5,
}

var provenanceWeights = map[string]float64{
    "confirmed":    1.0,
    "dynamic":      0.9,
    "static":       0.8,
    "conflict":     0.6,
    "":             0.8, // treat missing provenance as static
}

decay = 0.7 (default, overridable per request)
```

---

## Token budget pruning

Estimate tokens per node before including:
- Seed node with BehaviorSpec: 60 tokens
- Seed node without spec:      120 tokens (has full body)
- Peripheral node (hop >= 1):  20 tokens (signature only)

Sort all discovered nodes by score descending.
Include greedily until Σ estimated_tokens > max_tokens.

EXCEPTION: Always include conflict and dead-branch edges regardless of budget.
These are tagged on edges as provenance="conflict" or provenance="dead".
Their inclusion adds ~15 tokens per edge — acceptable overshoot.

---

## Collecting edges

After node pruning, collect all edges between surviving nodes:
```cypher
MATCH (a:Node)-[r]->(b:Node)
WHERE a.stable_id IN $surviving_ids
  AND b.stable_id IN $surviving_ids
RETURN a.stable_id, b.stable_id, type(r), r.source, r.frequency_ratio
```

Also fetch conflict/dead edges involving any surviving node (even if the target
was pruned — these always appear in the output):
```cypher
MATCH (a:Node)-[r]->(b:Node)
WHERE a.stable_id IN $surviving_ids
  AND r.source IN ['conflict', 'dead']
RETURN a.stable_id, b.stable_id, type(r), r.source, r.detail
```

---

## Service structure

```
services/subgraph-expander/
  cmd/main.go
  internal/
    expander/
      expander.go
      bfs.go
      scoring.go
      budget.go
    neo4j/
      client.go
      queries.go
    server/server.go
  gen/
  go.mod
  Dockerfile
```

---

## Verification

```bash
# Prerequisite: graph-writer has indexed at least one Python file with multiple functions

grpcurl -plaintext \
  -d '{
    "seeds": [
      {"stable_id": "<stable_id of authenticate>", "name": "authenticate", "type": "function", "score": 1.0, "retrieval_method": "both"}
    ],
    "query_type": "flow",
    "max_tokens": 2000,
    "hop_depth": 2,
    "decay": 0.7
  }' \
  localhost:8088 QueryService/Expand

# Verify:
# 1. authenticate is in nodes[] with hop=0
# 2. Functions called by authenticate are in nodes[] with hop=1
# 3. Functions called by hop-1 nodes are in nodes[] with hop=2
# 4. budget_used is <= max_tokens
# 5. edges[] contains confirmed/static/dynamic tagged edges
# 6. conflict edges appear even if they would exceed budget

# Impact query test
grpcurl -plaintext \
  -d '{"seeds":[{"stable_id":"<authenticate stable_id>","score":1.0}],"query_type":"impact","max_tokens":2000}' \
  localhost:8088 QueryService/Expand
# Expect: callers of authenticate in nodes[], not callees

# Unit tests
cd services/subgraph-expander && go test ./...
```

---

## Definition of done

- [ ] BFS traversal from a seed node returns nodes at correct hop depths
- [ ] Scoring: nodes at hop 0 score higher than hop 1 which scores higher than hop 2
- [ ] Confirmed edges score higher than static-only at same hop
- [ ] Token budget respected — output never exceeds max_tokens estimate
- [ ] Conflict/dead edges always included regardless of budget
- [ ] Impact queries follow CALLS in reverse
- [ ] edges[] collected correctly between surviving nodes
- [ ] All Go tests pass
- [ ] Dockerfile builds cleanly
