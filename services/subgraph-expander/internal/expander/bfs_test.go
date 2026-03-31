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
