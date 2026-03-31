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

// mockHydrator implements SeedHydrator.
type mockHydrator struct{}

func (m *mockHydrator) HydrateSeeds(ctx context.Context, stableIDs []string) (map[string]BFSState, error) {
	out := make(map[string]BFSState, len(stableIDs))
	for _, id := range stableIDs {
		out[id] = BFSState{
			StableID:  id,
			Body:      "hydrated body for " + id,
			Signature: "hydrated sig",
		}
	}
	return out, nil
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
	exp := New(fetcher, &mockHydrator{}, &mockSpecChecker{}, &mockEdgeCollector{})

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
	exp := New(fetcher, &mockHydrator{}, &mockSpecChecker{}, &mockEdgeCollector{})

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
	exp := New(fetcher, &mockHydrator{}, &mockSpecChecker{}, collector)

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
	exp := New(&mockFetcher{}, &mockHydrator{}, &mockSpecChecker{}, &mockEdgeCollector{})
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
	exp := New(&mockFetcher{}, &mockHydrator{}, &mockSpecChecker{}, &mockEdgeCollector{})
	seeds := []*querypb.SeedNode{{StableId: "seed1", Score: 1.0}}
	_, err := exp.Expand(context.Background(), seeds, "bogus", 2000, 1, 0.7)
	if !errors.Is(err, ErrInvalidQueryType) {
		t.Errorf("expected ErrInvalidQueryType, got %v", err)
	}
}

func TestExpander_SpecCheckerFailsDegradeGracefully(t *testing.T) {
	fetcher := &mockFetcher{neighbors: map[string][]BFSState{}}
	badSpec := &mockSpecChecker{err: fmt.Errorf("postgres down")}
	exp := New(fetcher, &mockHydrator{}, badSpec, &mockEdgeCollector{})

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
