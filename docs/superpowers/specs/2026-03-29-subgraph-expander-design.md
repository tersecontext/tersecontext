# Subgraph Expander — Design Spec

**Date:** 2026-03-29
**Service:** subgraph-expander
**Port:** 8088 (gRPC), 8089 (HTTP health)
**Language:** Go 1.23

---

## Overview

The subgraph expander receives seed nodes from the dual retriever and expands outward through the Neo4j knowledge graph via application-side BFS traversal. It scores every discovered node, prunes to a token budget, collects edges between surviving nodes, and returns a `RankedSubgraphResponse` for the serializer.

This is the most algorithmically complex service in the TerseContext query pipeline.

---

## gRPC Interface

Defined in `proto/query.proto`. The service implements `QueryService.Expand`:

```protobuf
rpc Expand(ExpandRequest) returns (RankedSubgraphResponse);

message ExpandRequest {
  SeedNodesResponse   seeds   = 1;
  QueryIntentResponse intent  = 2;  // intent.query_type used here
  int32               max_tokens = 3;
}
```

`hop_depth` and `decay` are not in the proto. They are service-level defaults, overridable via environment variables:
- `EXPANDER_HOP_DEPTH` (default: 2)
- `EXPANDER_DECAY` (default: 0.7)

---

## Package Structure

```
services/subgraph-expander/
  cmd/main.go
  internal/
    server/
      server.go          — implements QueryService; delegates to Expander
    expander/
      expander.go        — orchestrator: pre-fetch BehaviorSpecs, run BFS, prune, collect edges
      bfs.go             — BFS state machine, hop loop, visited-set management
      scoring.go         — weight tables, score formula
      budget.go          — token estimation, greedy pruning, conflict/dead exceptions
    neo4j/
      client.go          — driver setup, health check
      queries.go         — batchFetchNeighbors (per query_type), collectEdges, collectConflictEdges
    postgres/
      client.go          — pool setup, health check
      queries.go         — HasBehaviorSpec(stableIDs) → map[string]bool
  gen/                   — proto stubs (copied from dual-retriever/gen)
  go.mod
  Dockerfile
```

---

## Data Flow

1. `server.go` receives `ExpandRequest` → extracts `seeds.nodes`, `intent.query_type`, `max_tokens`
2. `expander.go` calls `postgres.HasBehaviorSpec` for all seed stable_ids
3. `bfs.go` runs the hop loop; calls `neo4j.batchFetchNeighbors` once per hop
4. `scoring.go` weights each discovered node: `parentScore × decay × edgeWeight × provenanceWeight`
5. `budget.go` sorts all visited nodes by score desc, greedily includes until `Σ estimated_tokens > max_tokens`
6. `neo4j.collectEdges` fetches edges between surviving nodes
7. `neo4j.collectConflictEdges` fetches conflict/dead edges involving any surviving node (always included)
8. Response assembled and returned

---

## BFS Algorithm

BFS is application-side — one Neo4j query per hop, not a single variable-length Cypher path.

```go
type BFSState struct {
    StableID       string
    Name           string
    Type           string
    Signature      string
    Docstring      string
    Body           string
    Score          float64
    Hop            int
    ParentID       string
    EdgeType       string
    Provenance     string
    ObservedCalls  int32
    AvgLatencyMs   float64
    BranchCoverage float64
    RaisesObserved []string
    SideEffects    []string
    FrequencyRatio float64
}

visited := map[string]BFSState{}
// seeds inserted at hop=0 with score=1.0

for hop := 0; hop <= hopDepth; hop++ {
    currentLevel := nodesAtHop(visited, hop)
    neighbors := batchFetchNeighbors(ctx, currentLevel, queryType)
    for _, n := range neighbors {
        if existing, seen := visited[n.StableID]; seen {
            if n.Score > existing.Score {
                visited[n.StableID] = n  // keep higher score
            }
            continue
        }
        n.Score = parentScore * decay * edgeTypeWeights[n.EdgeType] * provenanceWeights[n.Provenance]
        visited[n.StableID] = n
    }
}
```

### Edge Routing per query_type

| query_type | Direction | Relationship types |
|---|---|---|
| `flow` | forward `(seed)→(neighbor)` | `CALLS`, `IMPORTS`, `INHERITS` |
| `lookup` | forward `(seed)→(neighbor)` | `DEFINES`, `IMPORTS`, `INHERITS` |
| `impact` | reverse `(caller)→(seed)` | `CALLS`, `TESTED_BY` |

For `impact`, discovered nodes are the callers/testers of the seeds.

---

## Scoring Weights

These values are fixed. Do not change them.

```go
var edgeTypeWeights = map[string]float64{
    "CALLS":       1.0,
    "IMPORTS":     0.8,
    "INHERITS":    0.7,
    "MODIFIED_BY": 0.6,
    "TESTED_BY":   0.5,
}

var provenanceWeights = map[string]float64{
    "confirmed": 1.0,
    "dynamic":   0.9,
    "static":    0.8,
    "conflict":  0.6,
    "":          0.8,  // missing provenance treated as static
}
```

Score formula: `score = parentScore × decay × edgeTypeWeights[edgeType] × provenanceWeights[provenance]`

---

## Token Budget Pruning

Token estimates per node:
- Seed node with BehaviorSpec (from Postgres): **60 tokens**
- Seed node without BehaviorSpec: **120 tokens**
- Peripheral node (hop ≥ 1): **20 tokens**

Algorithm:
1. Sort all `visited` nodes by score descending
2. Greedily include nodes until `Σ estimated_tokens > max_tokens`
3. **Exception:** conflict/dead-provenance edges are always included regardless of budget; they add ~15 tokens per edge (acceptable overshoot)

`budget_used` in the response reflects the total estimated tokens of included nodes plus conflict/dead edge overhead.

---

## Edge Collection

After pruning, two queries run:

**Standard edges** (between surviving nodes only):
```cypher
MATCH (a:Node)-[r]->(b:Node)
WHERE a.stable_id IN $surviving_ids
  AND b.stable_id IN $surviving_ids
RETURN a.stable_id, b.stable_id, type(r), r.source, r.frequency_ratio
```

**Conflict/dead edges** (involving any surviving node, target may be pruned):
```cypher
MATCH (a:Node)-[r]->(b:Node)
WHERE a.stable_id IN $surviving_ids
  AND r.source IN ['conflict', 'dead']
RETURN a.stable_id, b.stable_id, type(r), r.source, r.detail
```

Conflict/dead edges are also emitted as `SubgraphWarning` entries in the response.

---

## Error Handling

| Condition | Behavior |
|---|---|
| Neo4j/Postgres unreachable at startup | Fatal log, process exits |
| Neo4j error during request | gRPC `Internal` with error detail |
| Postgres unavailable during request | Log warning; treat all seeds as having no BehaviorSpec |
| Empty seeds list | Return empty `RankedSubgraphResponse` (not an error) |
| Unknown `query_type` | gRPC `InvalidArgument` |

---

## Testing

Unit tests with mocked interfaces, same pattern as dual-retriever:

- `NeighborFetcher` interface wraps Neo4j neighbor queries
- `SpecChecker` interface wraps Postgres BehaviorSpec lookup

Key test cases:
- Nodes at hop 0 score higher than hop 1, which scores higher than hop 2
- Confirmed provenance scores higher than static at the same hop
- Token budget respected (node estimates never exceed max_tokens)
- Conflict/dead edges included even when budget is already full
- Impact query returns callers (reverse CALLS), not callees
- Unknown query_type returns `InvalidArgument`
- Duplicate neighbor (seen from two parents) keeps the higher-score version

---

## Deployment

**Environment variables:**

| Variable | Default | Description |
|---|---|---|
| `NEO4J_URI` | `bolt://neo4j:7687` | Neo4j bolt URI |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | `password` | Neo4j password |
| `POSTGRES_DSN` | — | Postgres connection string |
| `EXPANDER_HOP_DEPTH` | `2` | BFS hop depth |
| `EXPANDER_DECAY` | `0.7` | Score decay per hop |
| `GRPC_PORT` | `8088` | gRPC listen port |
| `HTTP_PORT` | `8089` | Health/metrics HTTP port |

**Dockerfile:** Multi-stage build, Go 1.23-bookworm builder → distroless static runtime, same pattern as dual-retriever.

**docker-compose.yml:** Added as `subgraph-expander` service, depends on `neo4j` and `postgres`.

---

## Definition of Done

- [ ] BFS traversal from a seed node returns nodes at correct hop depths
- [ ] Scoring: nodes at hop 0 score higher than hop 1 which scores higher than hop 2
- [ ] Confirmed edges score higher than static-only at same hop
- [ ] Token budget respected — output never exceeds max_tokens estimate (from nodes alone)
- [ ] Conflict/dead edges always included regardless of budget
- [ ] Impact queries follow CALLS in reverse
- [ ] edges[] collected correctly between surviving nodes
- [ ] All Go tests pass
- [ ] Dockerfile builds cleanly
- [ ] Service added to docker-compose.yml
