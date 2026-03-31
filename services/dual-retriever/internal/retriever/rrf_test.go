package retriever

import (
	"testing"
)

func TestRRF_BothLists(t *testing.T) {
	vector := []RankedNode{
		{StableID: "a", Name: "funcA", Type: "function", Source: "vector"},
		{StableID: "b", Name: "funcB", Type: "function", Source: "vector"},
		{StableID: "c", Name: "funcC", Type: "function", Source: "vector"},
	}
	graph := []RankedNode{
		{StableID: "b", Name: "funcB", Type: "function", Source: "graph"},
		{StableID: "d", Name: "funcD", Type: "function", Source: "graph"},
		{StableID: "a", Name: "funcA", Type: "function", Source: "graph"},
	}

	result := RRF([][]RankedNode{vector, graph}, 60, 10)

	if len(result) < 3 {
		t.Fatalf("expected at least 3 results, got %d", len(result))
	}
	if result[0].StableId != "b" {
		t.Errorf("expected first result to be 'b', got %q", result[0].StableId)
	}
	if result[1].StableId != "a" {
		t.Errorf("expected second result to be 'a', got %q", result[1].StableId)
	}

	methods := map[string]string{}
	for _, n := range result {
		methods[n.StableId] = n.RetrievalMethod
	}
	if methods["a"] != "both" {
		t.Errorf("expected 'a' retrieval_method='both', got %q", methods["a"])
	}
	if methods["b"] != "both" {
		t.Errorf("expected 'b' retrieval_method='both', got %q", methods["b"])
	}
	if methods["c"] != "vector" {
		t.Errorf("expected 'c' retrieval_method='vector', got %q", methods["c"])
	}
	if methods["d"] != "graph" {
		t.Errorf("expected 'd' retrieval_method='graph', got %q", methods["d"])
	}
}

func TestRRF_SingleList(t *testing.T) {
	vector := []RankedNode{
		{StableID: "a", Name: "funcA", Type: "function", Source: "vector"},
		{StableID: "b", Name: "funcB", Type: "function", Source: "vector"},
	}
	result := RRF([][]RankedNode{vector}, 60, 10)
	if len(result) != 2 {
		t.Fatalf("expected 2 results, got %d", len(result))
	}
	if result[0].RetrievalMethod != "vector" {
		t.Errorf("expected retrieval_method='vector', got %q", result[0].RetrievalMethod)
	}
}

func TestRRF_EmptyLists(t *testing.T) {
	result := RRF([][]RankedNode{}, 60, 10)
	if len(result) != 0 {
		t.Fatalf("expected 0 results, got %d", len(result))
	}
	result = RRF([][]RankedNode{{}, {}}, 60, 10)
	if len(result) != 0 {
		t.Fatalf("expected 0 results for empty sublists, got %d", len(result))
	}
}

func TestRRF_MaxSeedsCap(t *testing.T) {
	nodes := make([]RankedNode, 20)
	for i := range nodes {
		nodes[i] = RankedNode{
			StableID: string(rune('a' + i)),
			Name:     string(rune('a' + i)),
			Type:     "function",
			Source:   "vector",
		}
	}
	result := RRF([][]RankedNode{nodes}, 60, 5)
	if len(result) != 5 {
		t.Fatalf("expected 5 results (capped), got %d", len(result))
	}
}

func TestRRF_BothRanksHigherThanSingle(t *testing.T) {
	vector := []RankedNode{
		{StableID: "vector_only", Name: "vo", Type: "function", Source: "vector"},
		{StableID: "shared", Name: "sh", Type: "function", Source: "vector"},
	}
	graph := []RankedNode{
		{StableID: "shared", Name: "sh", Type: "function", Source: "graph"},
	}
	result := RRF([][]RankedNode{vector, graph}, 60, 10)
	if result[0].StableId != "shared" {
		t.Errorf("expected 'shared' to rank first (both lists), got %q", result[0].StableId)
	}
}
