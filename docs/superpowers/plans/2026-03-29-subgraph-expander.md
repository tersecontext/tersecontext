# Subgraph Expander Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the subgraph-expander gRPC service that performs application-side BFS through Neo4j, scores discovered nodes, prunes to a token budget, and returns a `RankedSubgraphResponse`.

**Architecture:** Application-side BFS (one Neo4j query per hop) with multiplicative scoring (`parentScore × decay × edgeWeight × provenanceWeight`), greedy token-budget pruning with conflict/dead-edge exceptions, and Postgres lookup for seed BehaviorSpec text. The service imports proto stubs from the dual-retriever module via the shared go.work workspace.

**Tech Stack:** Go 1.23, Neo4j Go driver v5, pgx/v5 (Postgres), gRPC/protobuf (stubs from dual-retriever/gen), standard library only for mocks in tests.

**Reference:** Spec at `docs/superpowers/specs/2026-03-29-subgraph-expander-design.md`. Pattern reference: `services/dual-retriever/`.

---

## File Map

| File | Responsibility |
|---|---|
| `services/subgraph-expander/go.mod` | Module deps (neo4j, pgx, grpc, dual-retriever) |
| `services/subgraph-expander/cmd/main.go` | Wires env config, drivers, gRPC + HTTP health servers |
| `services/subgraph-expander/internal/expander/scoring.go` | Edge/provenance weight tables and score formula |
| `services/subgraph-expander/internal/expander/scoring_test.go` | Unit tests for scoring |
| `services/subgraph-expander/internal/expander/budget.go` | Token estimation and greedy pruning |
| `services/subgraph-expander/internal/expander/budget_test.go` | Unit tests for budget pruning |
| `services/subgraph-expander/internal/expander/bfs.go` | BFSState type, hop loop, visited-set management |
| `services/subgraph-expander/internal/expander/bfs_test.go` | Unit tests for BFS traversal |
| `services/subgraph-expander/internal/expander/expander.go` | Orchestrator: SpecChecker + BFS + budget + edge collect |
| `services/subgraph-expander/internal/expander/expander_test.go` | Unit tests for orchestrator (all deps mocked) |
| `services/subgraph-expander/internal/neo4j/client.go` | Neo4j driver setup and health check |
| `services/subgraph-expander/internal/neo4j/queries.go` | Implements NeighborFetcher (batchFetchNeighbors, collectEdges, collectConflictEdges) |
| `services/subgraph-expander/internal/postgres/client.go` | pgx pool setup and health check |
| `services/subgraph-expander/internal/postgres/queries.go` | Implements SpecChecker (FetchBehaviorSpecs) |
| `services/subgraph-expander/internal/server/server.go` | Implements QueryService.Expand, stubs Understand/Retrieve/Serialize |
| `services/subgraph-expander/internal/server/server_test.go` | Unit tests for server handler |
| `services/subgraph-expander/Dockerfile` | Multi-stage Alpine build |
| `docker-compose.yml` | Add subgraph-expander service (modify existing) |

---

## Task 1: Module Bootstrap

**Files:**
- Modify: `services/subgraph-expander/go.mod`

- [ ] **Step 1: Write go.mod with all dependencies**

```
module github.com/tersecontext/tc/services/subgraph-expander

go 1.23

require (
	github.com/jackc/pgx/v5 v5.7.5
	github.com/neo4j/neo4j-go-driver/v5 v5.28.4
	github.com/tersecontext/tc/services/dual-retriever v0.0.0
	google.golang.org/grpc v1.79.3
	google.golang.org/protobuf v1.36.11
)
```

- [ ] **Step 2: Create all package directories**

```bash
mkdir -p services/subgraph-expander/cmd
mkdir -p services/subgraph-expander/internal/expander
mkdir -p services/subgraph-expander/internal/neo4j
mkdir -p services/subgraph-expander/internal/postgres
mkdir -p services/subgraph-expander/internal/server
mkdir -p services/subgraph-expander/gen
```

- [ ] **Step 3: Copy proto stubs from dual-retriever**

The `gen/` directory must live inside `services/subgraph-expander/` so the Dockerfile build context includes it (Docker has no access to the go.work workspace). Copy the generated files and update the `go_package` path in the copies to point to the subgraph-expander module:

```bash
cp services/dual-retriever/gen/query.pb.go   services/subgraph-expander/gen/
cp services/dual-retriever/gen/query_grpc.pb.go services/subgraph-expander/gen/
```

Then edit both copied files: replace the package declaration line from:
```go
// source: proto/query.proto
```
No text changes needed in the package declaration itself (it's `package querypb` in both). But the `go_package` option in the `.pb.go` file comments refers to `dual-retriever/gen`. This is fine — it is only a comment. The actual Go package path used in `go.mod` require must change.

Update `go.mod` to remove the `dual-retriever` require and add a local `replace` pointing the gen package to the copied files. Instead, simplify: **do not require `dual-retriever` as a module**. The `gen/` files are already copied. Change all import paths in `gen/*.go` from `github.com/tersecontext/tc/services/dual-retriever/gen` to `github.com/tersecontext/tc/services/subgraph-expander/gen`:

```bash
sed -i 's|github.com/tersecontext/tc/services/dual-retriever/gen|github.com/tersecontext/tc/services/subgraph-expander/gen|g' \
  services/subgraph-expander/gen/query.pb.go \
  services/subgraph-expander/gen/query_grpc.pb.go
```

Update `go.mod` to remove the dual-retriever require (no longer needed):

```
module github.com/tersecontext/tc/services/subgraph-expander

go 1.23

require (
	github.com/jackc/pgx/v5 v5.7.5
	github.com/neo4j/neo4j-go-driver/v5 v5.28.4
	google.golang.org/grpc v1.79.3
	google.golang.org/protobuf v1.36.11
)
```

All code in the service will import `querypb "github.com/tersecontext/tc/services/subgraph-expander/gen"`.

- [ ] **Step 4: Run go mod tidy to populate go.sum**

```bash
cd services/subgraph-expander && go mod tidy
```

Expected: `go.sum` created, no errors.

- [ ] **Step 5: Commit**

```bash
git add services/subgraph-expander/go.mod services/subgraph-expander/go.sum
git commit -m "feat(subgraph-expander): bootstrap module with dependencies"
```

---

## Task 2: Scoring

**Files:**
- Create: `services/subgraph-expander/internal/expander/scoring.go`
- Create: `services/subgraph-expander/internal/expander/scoring_test.go`

- [ ] **Step 1: Write the failing tests**

`services/subgraph-expander/internal/expander/scoring_test.go`:
```go
package expander

import (
	"math"
	"testing"
)

func TestEdgeTypeWeight_KnownTypes(t *testing.T) {
	cases := []struct {
		edgeType string
		want     float64
	}{
		{"CALLS", 1.0},
		{"IMPORTS", 0.8},
		{"INHERITS", 0.7},
		{"MODIFIED_BY", 0.6},
		{"TESTED_BY", 0.5},
	}
	for _, tc := range cases {
		got := edgeTypeWeight(tc.edgeType)
		if math.Abs(got-tc.want) > 1e-9 {
			t.Errorf("edgeTypeWeight(%q) = %v, want %v", tc.edgeType, got, tc.want)
		}
	}
}

func TestEdgeTypeWeight_Unknown(t *testing.T) {
	// unknown edge types get weight 0 (not traversed)
	if edgeTypeWeight("UNKNOWN") != 0 {
		t.Error("expected 0 for unknown edge type")
	}
}

func TestProvenanceWeight_KnownTypes(t *testing.T) {
	cases := []struct {
		prov string
		want float64
	}{
		{"confirmed", 1.0},
		{"dynamic", 0.9},
		{"static", 0.8},
		{"conflict", 0.6},
		{"", 0.8}, // missing = static
	}
	for _, tc := range cases {
		got := provenanceWeight(tc.prov)
		if math.Abs(got-tc.want) > 1e-9 {
			t.Errorf("provenanceWeight(%q) = %v, want %v", tc.prov, got, tc.want)
		}
	}
}

func TestComputeScore(t *testing.T) {
	// hop-1 CALLS confirmed: 1.0 * 0.7 * 1.0 * 1.0 = 0.7
	got := computeScore(1.0, 0.7, "CALLS", "confirmed")
	if math.Abs(got-0.7) > 1e-9 {
		t.Errorf("computeScore = %v, want 0.7", got)
	}
}

func TestComputeScore_ConfirmedBeatsStatic(t *testing.T) {
	confirmed := computeScore(1.0, 0.7, "CALLS", "confirmed")
	static := computeScore(1.0, 0.7, "CALLS", "static")
	if confirmed <= static {
		t.Errorf("confirmed (%v) should beat static (%v)", confirmed, static)
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/subgraph-expander && go test ./internal/expander/ -run TestEdge -v
```

Expected: compile error — `edgeTypeWeight` not defined.

- [ ] **Step 3: Implement scoring.go**

`services/subgraph-expander/internal/expander/scoring.go`:
```go
package expander

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
	"":          0.8,
}

func edgeTypeWeight(edgeType string) float64 {
	return edgeTypeWeights[edgeType]
}

func provenanceWeight(prov string) float64 {
	if w, ok := provenanceWeights[prov]; ok {
		return w
	}
	return provenanceWeights[""]
}

func computeScore(parentScore, decay float64, edgeType, provenance string) float64 {
	return parentScore * decay * edgeTypeWeight(edgeType) * provenanceWeight(provenance)
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd services/subgraph-expander && go test ./internal/expander/ -run "TestEdge|TestProvenance|TestComputeScore" -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/subgraph-expander/internal/expander/scoring.go \
        services/subgraph-expander/internal/expander/scoring_test.go
git commit -m "feat(subgraph-expander): add scoring weights and formula"
```

---

## Task 3: BFS State and Hop Loop

> **Moved before budget (Task 4) because `BFSState` is defined here and budget depends on it.**

**Files:**
- Create: `services/subgraph-expander/internal/expander/bfs.go`
- Create: `services/subgraph-expander/internal/expander/bfs_test.go`

- [ ] **Step 1: Write failing tests**

`services/subgraph-expander/internal/expander/bfs_test.go`:
```go
package expander

import (
	"context"
	"errors"
	"testing"
)

// mockFetcher implements NeighborFetcher for tests.
type mockFetcher struct {
	// neighbors maps parentID → list of neighbor states to return
	neighbors map[string][]BFSState
	err       error
}

func (m *mockFetcher) FetchNeighbors(ctx context.Context, stableIDs []string, queryType string) ([]BFSState, error) {
	if m.err != nil {
		return nil, m.err
	}
	var result []BFSState
	for _, id := range stableIDs {
		result = append(result, m.neighbors[id]...)
	}
	return result, nil
}

func TestBFS_HopDepths(t *testing.T) {
	// seed "a" → hop1 "b" → hop2 "c"
	fetcher := &mockFetcher{
		neighbors: map[string][]BFSState{
			"a": {{StableID: "b", ParentID: "a", EdgeType: "CALLS", Provenance: "static"}},
			"b": {{StableID: "c", ParentID: "b", EdgeType: "CALLS", Provenance: "static"}},
		},
	}
	seeds := []BFSState{{StableID: "a", Score: 1.0, Hop: 0}}
	visited, err := runBFS(context.Background(), seeds, fetcher, "flow", 2, 0.7)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if visited["a"].Hop != 0 {
		t.Errorf("seed should be hop 0")
	}
	if visited["b"].Hop != 1 {
		t.Errorf("b should be hop 1, got %d", visited["b"].Hop)
	}
	if visited["c"].Hop != 2 {
		t.Errorf("c should be hop 2, got %d", visited["c"].Hop)
	}
}

func TestBFS_ScoreDecaysWithHop(t *testing.T) {
	fetcher := &mockFetcher{
		neighbors: map[string][]BFSState{
			"a": {{StableID: "b", ParentID: "a", EdgeType: "CALLS", Provenance: "confirmed"}},
			"b": {{StableID: "c", ParentID: "b", EdgeType: "CALLS", Provenance: "confirmed"}},
		},
	}
	seeds := []BFSState{{StableID: "a", Score: 1.0, Hop: 0}}
	visited, _ := runBFS(context.Background(), seeds, fetcher, "flow", 2, 0.7)

	if visited["a"].Score <= visited["b"].Score {
		t.Errorf("hop0 score (%v) should exceed hop1 score (%v)", visited["a"].Score, visited["b"].Score)
	}
	if visited["b"].Score <= visited["c"].Score {
		t.Errorf("hop1 score (%v) should exceed hop2 score (%v)", visited["b"].Score, visited["c"].Score)
	}
}

func TestBFS_ConfirmedBeatsStatic(t *testing.T) {
	// "b" reachable via two paths; confirmed path should win
	fetcher := &mockFetcher{
		neighbors: map[string][]BFSState{
			"a": {
				{StableID: "b", ParentID: "a", EdgeType: "CALLS", Provenance: "confirmed"},
			},
			"c": {
				{StableID: "b", ParentID: "c", EdgeType: "CALLS", Provenance: "static"},
			},
		},
	}
	seeds := []BFSState{
		{StableID: "a", Score: 1.0, Hop: 0},
		{StableID: "c", Score: 1.0, Hop: 0},
	}
	visited, _ := runBFS(context.Background(), seeds, fetcher, "flow", 1, 0.7)
	// confirmed: 1.0 * 0.7 * 1.0 * 1.0 = 0.7
	// static:    1.0 * 0.7 * 1.0 * 0.8 = 0.56
	// b should have the confirmed score (0.7)
	if visited["b"].Provenance != "confirmed" {
		t.Errorf("expected b to keep confirmed provenance, got %q", visited["b"].Provenance)
	}
}

func TestBFS_HopDepthRespected(t *testing.T) {
	fetcher := &mockFetcher{
		neighbors: map[string][]BFSState{
			"a": {{StableID: "b", ParentID: "a", EdgeType: "CALLS", Provenance: "static"}},
			"b": {{StableID: "c", ParentID: "b", EdgeType: "CALLS", Provenance: "static"}},
		},
	}
	seeds := []BFSState{{StableID: "a", Score: 1.0, Hop: 0}}
	visited, _ := runBFS(context.Background(), seeds, fetcher, "flow", 1, 0.7) // hop_depth=1
	if _, ok := visited["c"]; ok {
		t.Error("c should not be visited at hop_depth=1")
	}
}

func TestBFS_FetchError(t *testing.T) {
	fetcher := &mockFetcher{err: errors.New("neo4j down")}
	seeds := []BFSState{{StableID: "a", Score: 1.0, Hop: 0}}
	_, err := runBFS(context.Background(), seeds, fetcher, "flow", 2, 0.7)
	if err == nil {
		t.Error("expected error from fetcher")
	}
}

func TestBFS_EmptySeeds(t *testing.T) {
	fetcher := &mockFetcher{neighbors: map[string][]BFSState{}}
	visited, err := runBFS(context.Background(), nil, fetcher, "flow", 2, 0.7)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(visited) != 0 {
		t.Errorf("expected empty visited map, got %d entries", len(visited))
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/subgraph-expander && go test ./internal/expander/ -run "TestBFS" -v
```

Expected: compile error — `BFSState`, `NeighborFetcher`, `runBFS` not defined.

- [ ] **Step 3: Implement bfs.go**

`services/subgraph-expander/internal/expander/bfs.go`:
```go
package expander

import "context"

// BFSState holds all data for a discovered node during BFS traversal.
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

// NeighborFetcher retrieves neighbors for a batch of nodes from the graph.
// The returned BFSState values have ParentID, EdgeType, and Provenance set;
// Score and Hop are set by the BFS loop.
type NeighborFetcher interface {
	FetchNeighbors(ctx context.Context, stableIDs []string, queryType string) ([]BFSState, error)
}

// runBFS performs application-side BFS from seed nodes up to hopDepth hops.
// Returns a visited map of stable_id → best-scoring BFSState.
func runBFS(ctx context.Context, seeds []BFSState, fetcher NeighborFetcher, queryType string, hopDepth int, decay float64) (map[string]BFSState, error) {
	visited := make(map[string]BFSState, len(seeds)*4)

	for _, s := range seeds {
		visited[s.StableID] = s
	}

	// hop < hopDepth (not <=): seeds are pre-seeded at hop 0, so iteration hop=0
	// fetches their neighbors (placing them at hop 1), ..., iteration hop=hopDepth-1
	// places nodes at hop=hopDepth. This correctly produces nodes up to the
	// requested depth. The spec pseudocode shows <= but that would produce an
	// extra level beyond hopDepth — this implementation is correct.
	for hop := 0; hop < hopDepth; hop++ {
		// Collect all nodes currently at this hop level.
		var currentIDs []string
		for _, n := range visited {
			if n.Hop == hop {
				currentIDs = append(currentIDs, n.StableID)
			}
		}
		if len(currentIDs) == 0 {
			break
		}

		neighbors, err := fetcher.FetchNeighbors(ctx, currentIDs, queryType)
		if err != nil {
			return nil, err
		}

		for _, n := range neighbors {
			parent := visited[n.ParentID]
			n.Score = computeScore(parent.Score, decay, n.EdgeType, n.Provenance)
			n.Hop = hop + 1

			if existing, seen := visited[n.StableID]; seen {
				if n.Score <= existing.Score {
					continue
				}
			}
			visited[n.StableID] = n
		}
	}

	return visited, nil
}

// visitedToSlice converts the visited map to a slice.
func visitedToSlice(visited map[string]BFSState) []BFSState {
	nodes := make([]BFSState, 0, len(visited))
	for _, n := range visited {
		nodes = append(nodes, n)
	}
	return nodes
}
```

- [ ] **Step 4: Run all expander tests to verify they pass**

```bash
cd services/subgraph-expander && go test ./internal/expander/ -v
```

Expected: all PASS (including budget tests from Task 3).

- [ ] **Step 5: Commit**

```bash
git add services/subgraph-expander/internal/expander/bfs.go \
        services/subgraph-expander/internal/expander/bfs_test.go
git commit -m "feat(subgraph-expander): add BFS traversal"
```

---

## Task 4: Token Budget

> **Depends on Task 3 — `BFSState` must be defined before this task compiles.**

**Files:**
- Create: `services/subgraph-expander/internal/expander/budget.go`
- Create: `services/subgraph-expander/internal/expander/budget_test.go`

- [ ] **Step 1: Write failing tests**

`services/subgraph-expander/internal/expander/budget_test.go`:
```go
package expander

import (
	"testing"
)

func TestEstimateTokens_SeedWithSpec(t *testing.T) {
	n := BFSState{Hop: 0}
	if estimateTokens(n, true) != 60 {
		t.Errorf("seed with spec should cost 60 tokens")
	}
}

func TestEstimateTokens_SeedWithoutSpec(t *testing.T) {
	n := BFSState{Hop: 0}
	if estimateTokens(n, false) != 120 {
		t.Errorf("seed without spec should cost 120 tokens")
	}
}

func TestEstimateTokens_PeripheralNode(t *testing.T) {
	n := BFSState{Hop: 1}
	if estimateTokens(n, false) != 20 {
		t.Errorf("peripheral node should cost 20 tokens")
	}
}

func TestPrune_RespectsBudget(t *testing.T) {
	nodes := []BFSState{
		{StableID: "a", Score: 1.0, Hop: 0},
		{StableID: "b", Score: 0.7, Hop: 1},
		{StableID: "c", Score: 0.5, Hop: 1},
		{StableID: "d", Score: 0.3, Hop: 1},
	}
	specMap := map[string]string{} // no specs
	// Budget: 120 (seed) + 20 (b) = 140; c would push to 160, d further
	// maxTokens=150 → include a and b only
	surviving, budgetUsed := prune(nodes, specMap, 150)
	if len(surviving) != 2 {
		t.Errorf("expected 2 surviving nodes, got %d", len(surviving))
	}
	if budgetUsed > 150 {
		t.Errorf("budget_used %d exceeds max_tokens 150", budgetUsed)
	}
}

func TestPrune_SortsByScoreDescending(t *testing.T) {
	nodes := []BFSState{
		{StableID: "low",  Score: 0.3, Hop: 1},
		{StableID: "high", Score: 0.9, Hop: 1},
		{StableID: "mid",  Score: 0.6, Hop: 1},
	}
	surviving, _ := prune(nodes, map[string]string{}, 1000)
	if surviving[0].StableID != "high" {
		t.Errorf("expected highest score first, got %q", surviving[0].StableID)
	}
}

func TestPrune_AlwaysIncludesSeeds(t *testing.T) {
	nodes := []BFSState{
		{StableID: "seed", Score: 1.0, Hop: 0},
		{StableID: "peer", Score: 0.9, Hop: 1},
	}
	surviving, _ := prune(nodes, map[string]string{}, 1) // budget too small for anything
	found := false
	for _, n := range surviving {
		if n.StableID == "seed" {
			found = true
		}
	}
	if !found {
		t.Error("seed node must always be included")
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/subgraph-expander && go test ./internal/expander/ -run "TestEstimate|TestPrune" -v
```

Expected: compile error — `estimateTokens` / `prune` not defined.

- [ ] **Step 3: Implement budget.go**

`services/subgraph-expander/internal/expander/budget.go`:
```go
package expander

import "sort"

const (
	tokensSeedWithSpec    = 60
	tokensSeedWithoutSpec = 120
	tokensPeripheral      = 20
	tokensConflictEdge    = 15
)

func estimateTokens(n BFSState, hasSpec bool) int {
	if n.Hop == 0 {
		if hasSpec {
			return tokensSeedWithSpec
		}
		return tokensSeedWithoutSpec
	}
	return tokensPeripheral
}

// prune sorts nodes by score descending and greedily includes them up to
// maxTokens. Seeds (hop=0) are always included regardless of budget.
func prune(nodes []BFSState, specMap map[string]string, maxTokens int) ([]BFSState, int) {
	sorted := make([]BFSState, len(nodes))
	copy(sorted, nodes)
	sort.Slice(sorted, func(i, j int) bool {
		return sorted[i].Score > sorted[j].Score
	})

	var surviving []BFSState
	used := 0
	for _, n := range sorted {
		_, hasSpec := specMap[n.StableID]
		cost := estimateTokens(n, hasSpec)
		if n.Hop == 0 || used+cost <= maxTokens {
			surviving = append(surviving, n)
			used += cost
		}
	}
	return surviving, used
}

// survivingIDs extracts stable_ids from a node slice.
func survivingIDs(nodes []BFSState) []string {
	ids := make([]string, len(nodes))
	for i, n := range nodes {
		ids[i] = n.StableID
	}
	return ids
}
```

- [ ] **Step 4: Run all expander tests**

```bash
cd services/subgraph-expander && go test ./internal/expander/ -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/subgraph-expander/internal/expander/budget.go \
        services/subgraph-expander/internal/expander/budget_test.go
git commit -m "feat(subgraph-expander): add token budget pruning"
```

---

## Task 5: Postgres Client and Queries

**Files:**
- Create: `services/subgraph-expander/internal/postgres/client.go`
- Create: `services/subgraph-expander/internal/postgres/queries.go`

No unit tests for the DB client itself — it is a thin wrapper tested end-to-end. The `SpecChecker` interface (used in Task 6) enables mock-based testing of callers.

- [ ] **Step 1: Write postgres/client.go**

`services/subgraph-expander/internal/postgres/client.go`:
```go
package postgres

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"
)

// Client wraps a pgx connection pool.
type Client struct {
	pool *pgxpool.Pool
}

// New creates a Client connected to the given DSN.
func New(ctx context.Context, dsn string) (*Client, error) {
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, fmt.Errorf("postgres: open pool: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("postgres: ping: %w", err)
	}
	return &Client{pool: pool}, nil
}

// Close closes the connection pool.
func (c *Client) Close() {
	c.pool.Close()
}
```

- [ ] **Step 2: Write postgres/queries.go**

`services/subgraph-expander/internal/postgres/queries.go`:
```go
package postgres

import "context"

// FetchBehaviorSpecs returns a map of stable_id → spec text for the given
// stable IDs. IDs with no behavior spec are absent from the map.
func (c *Client) FetchBehaviorSpecs(ctx context.Context, stableIDs []string) (map[string]string, error) {
	if len(stableIDs) == 0 {
		return map[string]string{}, nil
	}

	rows, err := c.pool.Query(ctx,
		`SELECT stable_id, spec FROM behavior_specs WHERE stable_id = ANY($1)`,
		stableIDs,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	result := make(map[string]string)
	for rows.Next() {
		var stableID, spec string
		if err := rows.Scan(&stableID, &spec); err != nil {
			return nil, err
		}
		result[stableID] = spec
	}
	return result, rows.Err()
}
```

- [ ] **Step 3: Verify it compiles**

```bash
cd services/subgraph-expander && go build ./internal/postgres/
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add services/subgraph-expander/internal/postgres/
git commit -m "feat(subgraph-expander): add postgres client and behavior spec query"
```

---

## Task 6: Neo4j Client and Queries

**Files:**
- Create: `services/subgraph-expander/internal/neo4j/client.go`
- Create: `services/subgraph-expander/internal/neo4j/queries.go`

- [ ] **Step 1: Write neo4j/client.go**

`services/subgraph-expander/internal/neo4j/client.go`:
```go
package neo4j

import (
	"context"
	"fmt"

	"github.com/neo4j/neo4j-go-driver/v5/neo4j"
)

// Client wraps a Neo4j driver.
type Client struct {
	driver neo4j.DriverWithContext
}

// New creates a Client and verifies connectivity.
func New(ctx context.Context, uri, user, password string) (*Client, error) {
	driver, err := neo4j.NewDriverWithContext(uri, neo4j.BasicAuth(user, password, ""))
	if err != nil {
		return nil, fmt.Errorf("neo4j: create driver: %w", err)
	}
	if err := driver.VerifyConnectivity(ctx); err != nil {
		driver.Close(ctx)
		return nil, fmt.Errorf("neo4j: verify connectivity: %w", err)
	}
	return &Client{driver: driver}, nil
}

// Close closes the driver.
func (c *Client) Close(ctx context.Context) {
	c.driver.Close(ctx)
}
```

- [ ] **Step 2: Write neo4j/queries.go**

`services/subgraph-expander/internal/neo4j/queries.go`:
```go
package neo4j

import (
	"context"
	"fmt"

	"github.com/neo4j/neo4j-go-driver/v5/neo4j"
	querypb "github.com/tersecontext/tc/services/subgraph-expander/gen"
	"github.com/tersecontext/tc/services/subgraph-expander/internal/expander"
)

// edgesByQueryType defines the relationship types and direction for each query type.
var forwardQueries = map[string]string{
	"flow":   "CALLS|IMPORTS|INHERITS",
	"lookup": "DEFINES|IMPORTS|INHERITS",
}

// FetchNeighbors fetches one hop of neighbors for the given stable IDs.
// For "impact" queries the direction is reversed (callers of seeds).
func (c *Client) FetchNeighbors(ctx context.Context, stableIDs []string, queryType string) ([]expander.BFSState, error) {
	session := c.driver.NewSession(ctx, neo4j.SessionConfig{AccessMode: neo4j.AccessModeRead})
	defer session.Close(ctx)

	var cypher string
	switch queryType {
	case "impact":
		cypher = `
			MATCH (caller:Node)-[r:CALLS|TESTED_BY]->(seed:Node)
			WHERE seed.stable_id IN $stable_ids
			  AND caller.active = true
			RETURN seed.stable_id   AS parent_id,
			       caller.stable_id AS stable_id,
			       caller.name      AS name,
			       caller.type      AS type,
			       caller.signature AS signature,
			       caller.docstring AS docstring,
			       caller.body      AS body,
			       type(r)          AS edge_type,
			       r.source         AS provenance,
			       r.frequency_ratio AS frequency_ratio`
	case "flow":
		cypher = `
			MATCH (seed:Node)-[r:CALLS|IMPORTS|INHERITS]->(neighbor:Node)
			WHERE seed.stable_id IN $stable_ids
			  AND neighbor.active = true
			RETURN seed.stable_id     AS parent_id,
			       neighbor.stable_id AS stable_id,
			       neighbor.name      AS name,
			       neighbor.type      AS type,
			       neighbor.signature AS signature,
			       neighbor.docstring AS docstring,
			       neighbor.body      AS body,
			       type(r)            AS edge_type,
			       r.source           AS provenance,
			       r.frequency_ratio  AS frequency_ratio`
	case "lookup":
		cypher = `
			MATCH (seed:Node)-[r:DEFINES|IMPORTS|INHERITS]->(neighbor:Node)
			WHERE seed.stable_id IN $stable_ids
			  AND neighbor.active = true
			RETURN seed.stable_id     AS parent_id,
			       neighbor.stable_id AS stable_id,
			       neighbor.name      AS name,
			       neighbor.type      AS type,
			       neighbor.signature AS signature,
			       neighbor.docstring AS docstring,
			       neighbor.body      AS body,
			       type(r)            AS edge_type,
			       r.source           AS provenance,
			       r.frequency_ratio  AS frequency_ratio`
	default:
		return nil, fmt.Errorf("unknown query_type: %q", queryType)
	}

	result, err := session.Run(ctx, cypher, map[string]interface{}{"stable_ids": stableIDs})
	if err != nil {
		return nil, fmt.Errorf("neo4j FetchNeighbors: %w", err)
	}

	var nodes []expander.BFSState
	for result.Next(ctx) {
		rec := result.Record()
		n := expander.BFSState{}
		if v, ok := rec.Get("stable_id"); ok && v != nil {
			n.StableID = v.(string)
		}
		if v, ok := rec.Get("parent_id"); ok && v != nil {
			n.ParentID = v.(string)
		}
		if v, ok := rec.Get("name"); ok && v != nil {
			n.Name = v.(string)
		}
		if v, ok := rec.Get("type"); ok && v != nil {
			n.Type = v.(string)
		}
		if v, ok := rec.Get("signature"); ok && v != nil {
			n.Signature = v.(string)
		}
		if v, ok := rec.Get("docstring"); ok && v != nil {
			n.Docstring = v.(string)
		}
		if v, ok := rec.Get("body"); ok && v != nil {
			n.Body = v.(string)
		}
		if v, ok := rec.Get("edge_type"); ok && v != nil {
			n.EdgeType = v.(string)
		}
		if v, ok := rec.Get("provenance"); ok && v != nil {
			n.Provenance = v.(string)
		}
		if v, ok := rec.Get("frequency_ratio"); ok && v != nil {
			if f, ok := v.(float64); ok {
				n.FrequencyRatio = f
			}
		}
		nodes = append(nodes, n)
	}
	return nodes, result.Err()
}

// CollectEdges fetches all edges between surviving nodes.
func (c *Client) CollectEdges(ctx context.Context, survivingIDs []string) ([]*querypb.SubgraphEdge, error) {
	if len(survivingIDs) == 0 {
		return nil, nil
	}
	session := c.driver.NewSession(ctx, neo4j.SessionConfig{AccessMode: neo4j.AccessModeRead})
	defer session.Close(ctx)

	result, err := session.Run(ctx, `
		MATCH (a:Node)-[r]->(b:Node)
		WHERE a.stable_id IN $ids AND b.stable_id IN $ids
		RETURN a.stable_id AS source, b.stable_id AS target,
		       type(r) AS type, r.source AS provenance, r.frequency_ratio AS frequency_ratio`,
		map[string]interface{}{"ids": survivingIDs},
	)
	if err != nil {
		return nil, fmt.Errorf("neo4j CollectEdges: %w", err)
	}

	var edges []*querypb.SubgraphEdge
	for result.Next(ctx) {
		rec := result.Record()
		e := &querypb.SubgraphEdge{}
		if v, ok := rec.Get("source"); ok && v != nil {
			e.Source = v.(string)
		}
		if v, ok := rec.Get("target"); ok && v != nil {
			e.Target = v.(string)
		}
		if v, ok := rec.Get("type"); ok && v != nil {
			e.Type = v.(string)
		}
		if v, ok := rec.Get("provenance"); ok && v != nil {
			e.Provenance = v.(string)
		}
		if v, ok := rec.Get("frequency_ratio"); ok && v != nil {
			if f, ok := v.(float64); ok {
				e.FrequencyRatio = f
			}
		}
		edges = append(edges, e)
	}
	return edges, result.Err()
}

// CollectConflictEdges fetches conflict/dead edges involving any surviving node.
// These edges always appear in the output regardless of budget.
func (c *Client) CollectConflictEdges(ctx context.Context, survivingIDs []string) ([]*querypb.SubgraphEdge, []*querypb.SubgraphWarning, error) {
	if len(survivingIDs) == 0 {
		return nil, nil, nil
	}
	session := c.driver.NewSession(ctx, neo4j.SessionConfig{AccessMode: neo4j.AccessModeRead})
	defer session.Close(ctx)

	result, err := session.Run(ctx, `
		MATCH (a:Node)-[r]->(b:Node)
		WHERE a.stable_id IN $ids
		  AND r.source IN ['conflict', 'dead']
		RETURN a.stable_id AS source, b.stable_id AS target,
		       type(r) AS type, r.source AS provenance, r.detail AS detail`,
		map[string]interface{}{"ids": survivingIDs},
	)
	if err != nil {
		return nil, nil, fmt.Errorf("neo4j CollectConflictEdges: %w", err)
	}

	var edges []*querypb.SubgraphEdge
	var warnings []*querypb.SubgraphWarning
	for result.Next(ctx) {
		rec := result.Record()
		src, _ := rec.Get("source")
		tgt, _ := rec.Get("target")
		typ, _ := rec.Get("type")
		prov, _ := rec.Get("provenance")
		detail, _ := rec.Get("detail")

		srcStr, _ := src.(string)
		tgtStr, _ := tgt.(string)
		typStr, _ := typ.(string)
		provStr, _ := prov.(string)
		detailStr, _ := detail.(string)

		edges = append(edges, &querypb.SubgraphEdge{
			Source:     srcStr,
			Target:     tgtStr,
			Type:       typStr,
			Provenance: provStr,
		})
		warnType := "CONFLICT"
		if provStr == "dead" {
			warnType = "DEAD"
		}
		warnings = append(warnings, &querypb.SubgraphWarning{
			Type:   warnType,
			Source: srcStr,
			Target: tgtStr,
			Detail: detailStr,
		})
	}
	return edges, warnings, result.Err()
}
```

- [ ] **Step 3: Verify it compiles**

```bash
cd services/subgraph-expander && go build ./internal/neo4j/
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add services/subgraph-expander/internal/neo4j/
git commit -m "feat(subgraph-expander): add neo4j client and BFS/edge queries"
```

---

## Task 7: Expander Orchestrator

**Files:**
- Create: `services/subgraph-expander/internal/expander/expander.go`
- Create: `services/subgraph-expander/internal/expander/expander_test.go`

- [ ] **Step 1: Write failing tests**

`services/subgraph-expander/internal/expander/expander_test.go`:
```go
package expander

import (
	"context"
	"errors"
	"fmt"
	"testing"

	querypb "github.com/tersecontext/tc/services/subgraph-expander/gen"
)

// mockSpecChecker implements SpecChecker.
type mockSpecChecker struct {
	specs map[string]string
	err   error
}

func (m *mockSpecChecker) FetchBehaviorSpecs(ctx context.Context, ids []string) (map[string]string, error) {
	if m.err != nil {
		return nil, m.err
	}
	result := make(map[string]string)
	for _, id := range ids {
		if s, ok := m.specs[id]; ok {
			result[id] = s
		}
	}
	return result, nil
}

// mockEdgeCollector implements EdgeCollector.
type mockEdgeCollector struct {
	edges         []*querypb.SubgraphEdge
	conflictEdges []*querypb.SubgraphEdge
	warnings      []*querypb.SubgraphWarning
}

func (m *mockEdgeCollector) CollectEdges(ctx context.Context, ids []string) ([]*querypb.SubgraphEdge, error) {
	return m.edges, nil
}

func (m *mockEdgeCollector) CollectConflictEdges(ctx context.Context, ids []string) ([]*querypb.SubgraphEdge, []*querypb.SubgraphWarning, error) {
	return m.conflictEdges, m.warnings, nil
}

func TestExpander_BasicFlow(t *testing.T) {
	fetcher := &mockFetcher{
		neighbors: map[string][]BFSState{
			"seed1": {{StableID: "n1", ParentID: "seed1", EdgeType: "CALLS", Provenance: "static"}},
		},
	}
	exp := New(fetcher, &mockSpecChecker{}, &mockEdgeCollector{})

	seeds := []*querypb.SeedNode{{StableId: "seed1", Name: "auth", Score: 1.0}}
	resp, err := exp.Expand(context.Background(), seeds, "flow", 2000, 2, 0.7)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(resp.Nodes) == 0 {
		t.Fatal("expected at least one node")
	}

	// seed must be in response
	var foundSeed bool
	for _, n := range resp.Nodes {
		if n.StableId == "seed1" && n.Hop == 0 {
			foundSeed = true
		}
	}
	if !foundSeed {
		t.Error("seed node must appear in response at hop=0")
	}
}

func TestExpander_BudgetRespected(t *testing.T) {
	// 10 peripheral nodes, budget only fits a few
	neighbors := make([]BFSState, 10)
	for i := range neighbors {
		neighbors[i] = BFSState{
			StableID:   fmt.Sprintf("n%d", i),
			ParentID:   "seed1",
			EdgeType:   "CALLS",
			Provenance: "static",
		}
	}
	// Use inline mock that ignores queryType
	fetcher := &mockFetcher{
		neighbors: map[string][]BFSState{"seed1": neighbors},
	}
	exp := New(fetcher, &mockSpecChecker{}, &mockEdgeCollector{})

	seeds := []*querypb.SeedNode{{StableId: "seed1", Score: 1.0}}
	// maxTokens=160: 120 (seed without spec) + 20 (one peripheral) = 140; two = 160
	resp, err := exp.Expand(context.Background(), seeds, "flow", 160, 1, 0.7)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resp.BudgetUsed > 160 {
		t.Errorf("budget_used %d exceeds max_tokens 160", resp.BudgetUsed)
	}
}

func TestExpander_ConflictEdgesAlwaysIncluded(t *testing.T) {
	fetcher := &mockFetcher{neighbors: map[string][]BFSState{}}
	conflictEdge := &querypb.SubgraphEdge{Source: "seed1", Target: "other", Provenance: "conflict"}
	warning := &querypb.SubgraphWarning{Type: "CONFLICT", Source: "seed1", Target: "other"}
	collector := &mockEdgeCollector{
		conflictEdges: []*querypb.SubgraphEdge{conflictEdge},
		warnings:      []*querypb.SubgraphWarning{warning},
	}
	exp := New(fetcher, &mockSpecChecker{}, collector)

	seeds := []*querypb.SeedNode{{StableId: "seed1", Score: 1.0}}
	resp, err := exp.Expand(context.Background(), seeds, "flow", 1, 1, 0.7) // tiny budget
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(resp.Warnings) == 0 {
		t.Error("conflict warnings must appear even when budget is exhausted")
	}
}

func TestExpander_EmptySeeds(t *testing.T) {
	exp := New(&mockFetcher{}, &mockSpecChecker{}, &mockEdgeCollector{})
	resp, err := exp.Expand(context.Background(), nil, "flow", 2000, 2, 0.7)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(resp.Nodes) != 0 {
		t.Errorf("expected 0 nodes for empty seeds, got %d", len(resp.Nodes))
	}
}

func TestExpander_UnknownQueryType(t *testing.T) {
	// mockFetcher error is unreachable since validation fires before BFS.
	exp := New(&mockFetcher{}, &mockSpecChecker{}, &mockEdgeCollector{})
	seeds := []*querypb.SeedNode{{StableId: "seed1", Score: 1.0}}
	_, err := exp.Expand(context.Background(), seeds, "bogus", 2000, 1, 0.7)
	if !errors.Is(err, ErrInvalidQueryType) {
		t.Errorf("expected ErrInvalidQueryType, got %v", err)
	}
}

func TestExpander_SpecCheckerFailsDegradeGracefully(t *testing.T) {
	fetcher := &mockFetcher{neighbors: map[string][]BFSState{}}
	badSpec := &mockSpecChecker{err: fmt.Errorf("postgres down")}
	exp := New(fetcher, badSpec, &mockEdgeCollector{})

	seeds := []*querypb.SeedNode{{StableId: "seed1", Score: 1.0}}
	// Should not error; should treat seed as having no spec
	resp, err := exp.Expand(context.Background(), seeds, "flow", 2000, 2, 0.7)
	if err != nil {
		t.Fatalf("expected graceful degradation, got: %v", err)
	}
	if len(resp.Nodes) == 0 {
		t.Error("expected seed node even when spec checker fails")
	}
}
```

Add `"fmt"` import to the test file — this is already needed for `fmt.Sprintf` in TestExpander_BudgetRespected.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/subgraph-expander && go test ./internal/expander/ -run "TestExpander" -v
```

Expected: compile error — `New`, `SpecChecker`, `EdgeCollector` not defined in expander package.

- [ ] **Step 3: Implement expander.go**

`services/subgraph-expander/internal/expander/expander.go`:
```go
package expander

import (
	"context"
	"errors"
	"fmt"
	"log/slog"

	querypb "github.com/tersecontext/tc/services/subgraph-expander/gen"
)

// ErrInvalidQueryType is returned for unrecognized query_type values.
// The server maps this to codes.InvalidArgument.
var ErrInvalidQueryType = errors.New("invalid query_type")

// SpecChecker fetches behavior spec text for a list of stable IDs.
// Returns a map of stable_id → spec text. IDs with no spec are absent.
type SpecChecker interface {
	FetchBehaviorSpecs(ctx context.Context, stableIDs []string) (map[string]string, error)
}

// EdgeCollector fetches edges between surviving nodes and conflict/dead edges.
type EdgeCollector interface {
	CollectEdges(ctx context.Context, survivingIDs []string) ([]*querypb.SubgraphEdge, error)
	CollectConflictEdges(ctx context.Context, survivingIDs []string) ([]*querypb.SubgraphEdge, []*querypb.SubgraphWarning, error)
}

// Expander orchestrates BFS traversal, scoring, pruning, and edge collection.
type Expander struct {
	fetcher   NeighborFetcher
	specs     SpecChecker
	collector EdgeCollector
}

// New creates an Expander with the given dependencies.
func New(fetcher NeighborFetcher, specs SpecChecker, collector EdgeCollector) *Expander {
	return &Expander{fetcher: fetcher, specs: specs, collector: collector}
}

// Expand performs BFS from seeds, prunes to budget, collects edges.
func (e *Expander) Expand(
	ctx context.Context,
	seeds []*querypb.SeedNode,
	queryType string,
	maxTokens int,
	hopDepth int,
	decay float64,
) (*querypb.RankedSubgraphResponse, error) {
	if len(seeds) == 0 {
		return &querypb.RankedSubgraphResponse{}, nil
	}

	// Validate query_type before touching the database.
	switch queryType {
	case "flow", "lookup", "impact":
		// valid
	default:
		return nil, fmt.Errorf("unknown query_type %q: %w", queryType, ErrInvalidQueryType)
	}

	// Convert proto seeds to BFSState at hop 0 with score 1.0.
	seedIDs := make([]string, len(seeds))
	bfsSeeds := make([]BFSState, len(seeds))
	for i, s := range seeds {
		seedIDs[i] = s.GetStableId()
		bfsSeeds[i] = BFSState{
			StableID: s.GetStableId(),
			Name:     s.GetName(),
			Type:     s.GetType(),
			Score:    1.0,
			Hop:      0,
		}
	}

	// Fetch behavior specs for seeds (graceful degradation on error).
	specMap, err := e.specs.FetchBehaviorSpecs(ctx, seedIDs)
	if err != nil {
		slog.Warn("behavior spec lookup failed, treating seeds as having no spec", "error", err)
		specMap = map[string]string{}
	}

	// BFS traversal.
	visited, err := runBFS(ctx, bfsSeeds, e.fetcher, queryType, hopDepth, decay)
	if err != nil {
		return nil, fmt.Errorf("BFS traversal: %w", err)
	}

	allNodes := visitedToSlice(visited)
	totalCandidates := len(allNodes)

	// Prune to budget.
	surviving, budgetUsed := prune(allNodes, specMap, maxTokens)
	ids := survivingIDs(surviving)

	// Collect standard edges between surviving nodes.
	edges, err := e.collector.CollectEdges(ctx, ids)
	if err != nil {
		return nil, fmt.Errorf("collect edges: %w", err)
	}

	// Collect conflict/dead edges (always included, may overshoot budget).
	conflictEdges, warnings, err := e.collector.CollectConflictEdges(ctx, ids)
	if err != nil {
		return nil, fmt.Errorf("collect conflict edges: %w", err)
	}
	edges = append(edges, conflictEdges...)
	budgetUsed += len(conflictEdges) * tokensConflictEdge

	// Build response nodes.
	respNodes := make([]*querypb.SubgraphNode, 0, len(surviving))
	for _, n := range surviving {
		rn := &querypb.SubgraphNode{
			StableId:       n.StableID,
			Name:           n.Name,
			Type:           n.Type,
			Signature:      n.Signature,
			Docstring:      n.Docstring,
			Body:           n.Body,
			Score:          n.Score,
			Hop:            int32(n.Hop),
			Provenance:     n.Provenance,
			ObservedCalls:  n.ObservedCalls,
			AvgLatencyMs:   n.AvgLatencyMs,
			BranchCoverage: n.BranchCoverage,
			RaisesObserved: n.RaisesObserved,
			SideEffects:    n.SideEffects,
		}
		if spec, ok := specMap[n.StableID]; ok {
			rn.BehaviorSpec = spec
		}
		respNodes = append(respNodes, rn)
	}

	return &querypb.RankedSubgraphResponse{
		Nodes:           respNodes,
		Edges:           edges,
		Warnings:        warnings,
		BudgetUsed:      int32(budgetUsed),
		TotalCandidates: int32(totalCandidates),
	}, nil
}
```

- [ ] **Step 4: Run all expander tests**

```bash
cd services/subgraph-expander && go test ./internal/expander/ -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/subgraph-expander/internal/expander/expander.go \
        services/subgraph-expander/internal/expander/expander_test.go
git commit -m "feat(subgraph-expander): add expander orchestrator"
```

---

## Task 8: gRPC Server

**Files:**
- Create: `services/subgraph-expander/internal/server/server.go`
- Create: `services/subgraph-expander/internal/server/server_test.go`

- [ ] **Step 1: Write failing tests**

`services/subgraph-expander/internal/server/server_test.go`:
```go
package server

import (
	"context"
	"fmt"
	"testing"

	"github.com/tersecontext/tc/services/subgraph-expander/internal/expander"
	querypb "github.com/tersecontext/tc/services/subgraph-expander/gen"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type mockExpander struct {
	resp *querypb.RankedSubgraphResponse
	err  error
}

func (m *mockExpander) Expand(ctx context.Context, seeds []*querypb.SeedNode, queryType string, maxTokens, hopDepth int, decay float64) (*querypb.RankedSubgraphResponse, error) {
	return m.resp, m.err
}

func TestServer_Expand_OK(t *testing.T) {
	exp := &mockExpander{
		resp: &querypb.RankedSubgraphResponse{
			Nodes: []*querypb.SubgraphNode{{StableId: "a", Hop: 0, Score: 1.0}},
		},
	}
	srv := NewQueryServer(exp, 2, 0.7)

	resp, err := srv.Expand(context.Background(), &querypb.ExpandRequest{
		Seeds:  &querypb.SeedNodesResponse{Nodes: []*querypb.SeedNode{{StableId: "a", Score: 1.0}}},
		Intent: &querypb.QueryIntentResponse{QueryType: "flow"},
		MaxTokens: 2000,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(resp.Nodes) != 1 {
		t.Fatalf("expected 1 node, got %d", len(resp.Nodes))
	}
}

func TestServer_Expand_DefaultMaxTokens(t *testing.T) {
	capture := &captureExpander{}
	srv := NewQueryServer(capture, 2, 0.7)

	_, _ = srv.Expand(context.Background(), &querypb.ExpandRequest{
		Seeds:     &querypb.SeedNodesResponse{},
		Intent:    &querypb.QueryIntentResponse{QueryType: "flow"},
		MaxTokens: 0, // should default to 2000
	})
	if capture.maxTokens != 2000 {
		t.Errorf("expected default maxTokens=2000, got %d", capture.maxTokens)
	}
}

type captureExpander struct {
	maxTokens int
}

func (c *captureExpander) Expand(ctx context.Context, seeds []*querypb.SeedNode, queryType string, maxTokens, hopDepth int, decay float64) (*querypb.RankedSubgraphResponse, error) {
	c.maxTokens = maxTokens
	return &querypb.RankedSubgraphResponse{}, nil
}

func TestServer_Understand_Unimplemented(t *testing.T) {
	srv := NewQueryServer(&mockExpander{resp: &querypb.RankedSubgraphResponse{}}, 2, 0.7)
	_, err := srv.Understand(context.Background(), &querypb.UnderstandRequest{})
	if status.Code(err) != codes.Unimplemented {
		t.Errorf("expected Unimplemented, got %v", err)
	}
}

func TestServer_Retrieve_Unimplemented(t *testing.T) {
	srv := NewQueryServer(&mockExpander{resp: &querypb.RankedSubgraphResponse{}}, 2, 0.7)
	_, err := srv.Retrieve(context.Background(), &querypb.RetrieveRequest{})
	if status.Code(err) != codes.Unimplemented {
		t.Errorf("expected Unimplemented, got %v", err)
	}
}

func TestServer_Serialize_Unimplemented(t *testing.T) {
	srv := NewQueryServer(&mockExpander{resp: &querypb.RankedSubgraphResponse{}}, 2, 0.7)
	_, err := srv.Serialize(context.Background(), &querypb.SerializeRequest{})
	if status.Code(err) != codes.Unimplemented {
		t.Errorf("expected Unimplemented, got %v", err)
	}
}

func TestServer_Expand_InvalidQueryType(t *testing.T) {
	exp := &mockExpander{err: fmt.Errorf("unknown query_type: %w", expander.ErrInvalidQueryType)}
	srv := NewQueryServer(exp, 2, 0.7)
	_, err := srv.Expand(context.Background(), &querypb.ExpandRequest{
		Seeds:  &querypb.SeedNodesResponse{},
		Intent: &querypb.QueryIntentResponse{QueryType: "bogus"},
	})
	if status.Code(err) != codes.InvalidArgument {
		t.Errorf("expected InvalidArgument for unknown query_type, got %v", err)
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/subgraph-expander && go test ./internal/server/ -v
```

Expected: compile error.

- [ ] **Step 3: Implement server.go**

`services/subgraph-expander/internal/server/server.go`:
```go
package server

import (
	"context"
	"errors"

	"github.com/tersecontext/tc/services/subgraph-expander/internal/expander"
	querypb "github.com/tersecontext/tc/services/subgraph-expander/gen"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

const defaultMaxTokens = 2000

// ExpanderService is the domain interface the server delegates to.
type ExpanderService interface {
	Expand(ctx context.Context, seeds []*querypb.SeedNode, queryType string, maxTokens, hopDepth int, decay float64) (*querypb.RankedSubgraphResponse, error)
}

// QueryServer implements QueryServiceServer for the subgraph-expander.
type QueryServer struct {
	querypb.UnimplementedQueryServiceServer
	expander ExpanderService
	hopDepth int
	decay    float64
}

// NewQueryServer creates a QueryServer with the given expander and defaults.
func NewQueryServer(expander ExpanderService, hopDepth int, decay float64) *QueryServer {
	return &QueryServer{expander: expander, hopDepth: hopDepth, decay: decay}
}

func (s *QueryServer) Expand(ctx context.Context, req *querypb.ExpandRequest) (*querypb.RankedSubgraphResponse, error) {
	maxTokens := int(req.GetMaxTokens())
	if maxTokens <= 0 {
		maxTokens = defaultMaxTokens
	}

	seeds := req.GetSeeds().GetNodes()
	queryType := req.GetIntent().GetQueryType()

	resp, err := s.expander.Expand(ctx, seeds, queryType, maxTokens, s.hopDepth, s.decay)
	if err != nil {
		if errors.Is(err, expander.ErrInvalidQueryType) {
			return nil, status.Errorf(codes.InvalidArgument, "expand: %v", err)
		}
		return nil, status.Errorf(codes.Internal, "expand: %v", err)
	}
	return resp, nil
}

func (s *QueryServer) Understand(ctx context.Context, _ *querypb.UnderstandRequest) (*querypb.QueryIntentResponse, error) {
	return nil, status.Error(codes.Unimplemented, "Understand not implemented by subgraph-expander")
}

func (s *QueryServer) Retrieve(ctx context.Context, _ *querypb.RetrieveRequest) (*querypb.SeedNodesResponse, error) {
	return nil, status.Error(codes.Unimplemented, "Retrieve not implemented by subgraph-expander")
}

func (s *QueryServer) Serialize(ctx context.Context, _ *querypb.SerializeRequest) (*querypb.ContextDocResponse, error) {
	return nil, status.Error(codes.Unimplemented, "Serialize not implemented by subgraph-expander")
}
```

- [ ] **Step 4: Run server tests**

```bash
cd services/subgraph-expander && go test ./internal/server/ -v
```

Expected: all PASS.

- [ ] **Step 5: Run all tests**

```bash
cd services/subgraph-expander && go test ./...
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add services/subgraph-expander/internal/server/
git commit -m "feat(subgraph-expander): add gRPC server handler"
```

---

## Task 9: Main Entry Point

**Files:**
- Create: `services/subgraph-expander/cmd/main.go`

No unit test — wiring code tested via integration (docker-compose + grpcurl).

- [ ] **Step 1: Write cmd/main.go**

`services/subgraph-expander/cmd/main.go`:
```go
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"sync/atomic"
	"syscall"

	querypb "github.com/tersecontext/tc/services/subgraph-expander/gen"
	"github.com/tersecontext/tc/services/subgraph-expander/internal/expander"
	neo4jclient "github.com/tersecontext/tc/services/subgraph-expander/internal/neo4j"
	pgclient "github.com/tersecontext/tc/services/subgraph-expander/internal/postgres"
	"github.com/tersecontext/tc/services/subgraph-expander/internal/server"
	"google.golang.org/grpc"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
)

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envFloat(key string, fallback float64) float64 {
	if v := os.Getenv(key); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return fallback
}

func envInt(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if i, err := strconv.Atoi(v); err == nil {
			return i
		}
	}
	return fallback
}

func main() {
	grpcPort := env("GRPC_PORT", "8088")
	neo4jURI := env("NEO4J_URI", "bolt://neo4j:7687")
	neo4jUser := env("NEO4J_USER", "neo4j")
	neo4jPassword := env("NEO4J_PASSWORD", "localpassword")
	postgresDSN := env("POSTGRES_DSN", "")
	hopDepth := envInt("EXPANDER_HOP_DEPTH", 2)
	decay := envFloat("EXPANDER_DECAY", 0.7)

	ctx := context.Background()
	var ready atomic.Bool

	// --- Neo4j ---
	neo4jClient, err := neo4jclient.New(ctx, neo4jURI, neo4jUser, neo4jPassword)
	if err != nil {
		slog.Error("failed to connect to neo4j", "error", err)
		os.Exit(1)
	}
	defer neo4jClient.Close(ctx)

	// --- Postgres ---
	var pgClient *pgclient.Client
	if postgresDSN != "" {
		pgClient, err = pgclient.New(ctx, postgresDSN)
		if err != nil {
			slog.Error("failed to connect to postgres", "error", err)
			os.Exit(1)
		}
		defer pgClient.Close()
	} else {
		slog.Warn("POSTGRES_DSN not set; behavior spec lookups will be skipped")
	}

	// --- Build expander ---
	var specChecker expander.SpecChecker = &noopSpecChecker{}
	if pgClient != nil {
		specChecker = pgClient
	}
	exp := expander.New(neo4jClient, specChecker, neo4jClient)

	// --- gRPC server ---
	lis, err := net.Listen("tcp", ":"+grpcPort)
	if err != nil {
		slog.Error("failed to listen", "port", grpcPort, "error", err)
		os.Exit(1)
	}

	grpcServer := grpc.NewServer()
	querypb.RegisterQueryServiceServer(grpcServer, server.NewQueryServer(exp, hopDepth, decay))

	healthServer := health.NewServer()
	healthpb.RegisterHealthServer(grpcServer, healthServer)

	ready.Store(true)
	healthServer.SetServingStatus("", healthpb.HealthCheckResponse_SERVING)

	// --- HTTP health server ---
	httpPort, _ := strconv.Atoi(grpcPort)
	httpAddr := fmt.Sprintf(":%d", httpPort+1)

	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	})
	mux.HandleFunc("GET /ready", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if ready.Load() {
			json.NewEncoder(w).Encode(map[string]string{"status": "ready"})
		} else {
			w.WriteHeader(http.StatusServiceUnavailable)
			json.NewEncoder(w).Encode(map[string]string{"status": "not ready"})
		}
	})
	mux.HandleFunc("GET /metrics", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})

	httpServer := &http.Server{Addr: httpAddr, Handler: mux}
	go func() {
		slog.Info("HTTP health server starting", "addr", httpAddr)
		if err := httpServer.ListenAndServe(); err != http.ErrServerClosed {
			slog.Error("HTTP server error", "error", err)
		}
	}()

	// --- Graceful shutdown ---
	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
		sig := <-sigCh
		slog.Info("shutting down", "signal", sig)
		healthServer.SetServingStatus("", healthpb.HealthCheckResponse_NOT_SERVING)
		grpcServer.GracefulStop()
		httpServer.Shutdown(ctx)
	}()

	slog.Info("gRPC server starting", "port", grpcPort)
	if err := grpcServer.Serve(lis); err != nil {
		slog.Error("gRPC server error", "error", err)
		os.Exit(1)
	}
}

// noopSpecChecker is used when Postgres is not configured.
type noopSpecChecker struct{}

func (n *noopSpecChecker) FetchBehaviorSpecs(_ context.Context, _ []string) (map[string]string, error) {
	return map[string]string{}, nil
}
```

- [ ] **Step 2: Build to verify it compiles**

```bash
cd services/subgraph-expander && go build ./cmd/main.go
```

Expected: binary created, no errors.

- [ ] **Step 3: Run all tests one final time**

```bash
cd services/subgraph-expander && go test ./...
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add services/subgraph-expander/cmd/main.go
git commit -m "feat(subgraph-expander): add main entry point"
```

---

## Task 10: Dockerfile and docker-compose

**Files:**
- Create: `services/subgraph-expander/Dockerfile`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Write Dockerfile**

`services/subgraph-expander/Dockerfile`:
```dockerfile
FROM golang:1.23-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o subgraph-expander ./cmd/main.go

FROM alpine:3.19
RUN apk add --no-cache ca-certificates
COPY --from=builder /app/subgraph-expander /usr/local/bin/subgraph-expander
EXPOSE 8088 8089
CMD ["subgraph-expander"]
```

- [ ] **Step 2: Fix dual-retriever port conflict in docker-compose.yml**

The dual-retriever currently exposes host port 8088 for its HTTP health server (the container internally listens on `grpc_port+1 = 8088`). The subgraph-expander needs host port 8088 for gRPC. Remove the `"8088:8088"` host binding from dual-retriever's `ports:` block — the internal health port still works for Docker-network healthchecks:

In `docker-compose.yml`, change the `dual-retriever` ports from:
```yaml
    ports:
      - "8087:8087"
      - "8088:8088"
```
to:
```yaml
    ports:
      - "8087:8087"
```

The dual-retriever healthcheck uses `http://localhost:8088/health` which refers to the container's own port — no change needed there.

- [ ] **Step 3: Add subgraph-expander to docker-compose.yml**

In `docker-compose.yml`, append after the `dual-retriever` service block (before the `neo4j-init` block):

```yaml
  subgraph-expander:
    build: ./services/subgraph-expander
    ports:
      - "8088:8088"
      - "8089:8089"
    environment:
      NEO4J_URI: bolt://neo4j:7687
      NEO4J_USER: neo4j
      NEO4J_PASSWORD: localpassword
      POSTGRES_DSN: postgres://tersecontext:localpassword@postgres:5432/tersecontext
      GRPC_PORT: "8088"
      EXPANDER_HOP_DEPTH: "2"
      EXPANDER_DECAY: "0.7"
    depends_on:
      neo4j:
        condition: service_healthy
      postgres:
        condition: service_healthy
    networks:
      - tersecontext
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:8089/health"]
      interval: 10s
      timeout: 5s
      retries: 5
```

- [ ] **Step 4: Build the Docker image**

```bash
cd services/subgraph-expander && docker build -t subgraph-expander:test .
```

Expected: build succeeds, no errors.

- [ ] **Step 5: Commit**

```bash
git add services/subgraph-expander/Dockerfile docker-compose.yml
git commit -m "feat(subgraph-expander): add Dockerfile and docker-compose service"
```

---

## Task 11: End-to-End Verification

Prerequisites: graph-writer has indexed at least one Python file with multiple functions. Run `make up` to start the stack.

- [ ] **Step 1: Verify health endpoint**

```bash
curl -s http://localhost:8089/health
```

Expected: `{"status":"ok"}`

- [ ] **Step 2: Flow query — find a seed stable_id**

```bash
# Get a stable_id from Neo4j browser at http://localhost:7474
# or via cypher-shell:
docker exec -it tc-subgraph-expander-neo4j-1 \
  cypher-shell -u neo4j -p localpassword \
  "MATCH (n:Node {active: true}) RETURN n.stable_id, n.name LIMIT 5"
```

- [ ] **Step 3: Run flow expand**

```bash
grpcurl -plaintext \
  -d '{
    "seeds": {"nodes": [{"stable_id": "<stable_id>", "name": "<name>", "score": 1.0}]},
    "intent": {"query_type": "flow"},
    "max_tokens": 2000
  }' \
  localhost:8088 tersecontext.query.QueryService/Expand
```

Expected:
1. `nodes[]` contains seed at `hop=0` with `score=1.0`
2. Functions called by seed are in `nodes[]` at `hop=1`
3. `budget_used <= 2000`
4. `edges[]` contains edges between surviving nodes

- [ ] **Step 4: Run impact expand**

```bash
grpcurl -plaintext \
  -d '{
    "seeds": {"nodes": [{"stable_id": "<stable_id>", "score": 1.0}]},
    "intent": {"query_type": "impact"},
    "max_tokens": 2000
  }' \
  localhost:8088 tersecontext.query.QueryService/Expand
```

Expected: `nodes[]` contains callers of the seed, not callees.

- [ ] **Step 5: Verify all unit tests pass**

```bash
cd services/subgraph-expander && go test ./...
```

Expected: all PASS.

---

## Definition of Done Checklist

- [ ] BFS traversal returns nodes at correct hop depths
- [ ] Nodes at hop 0 score higher than hop 1 which scores higher than hop 2
- [ ] Confirmed edges score higher than static-only at same hop
- [ ] Token budget respected (node estimates never exceed max_tokens)
- [ ] Conflict/dead edges always included regardless of budget
- [ ] Impact queries follow CALLS in reverse
- [ ] edges[] collected correctly between surviving nodes
- [ ] All Go tests pass (`go test ./...`)
- [ ] Dockerfile builds cleanly
- [ ] Service added to docker-compose.yml and health check passes
