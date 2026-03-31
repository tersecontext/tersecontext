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
