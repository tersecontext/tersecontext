package retriever

import (
	"context"
	"errors"
	"testing"
	"time"
)

// --- mocks ---

type mockEmbedder struct {
	vector []float32
	err    error
	delay  time.Duration
}

func (m *mockEmbedder) Embed(ctx context.Context, text string) ([]float32, error) {
	if m.delay > 0 {
		select {
		case <-time.After(m.delay):
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
	return m.vector, m.err
}

type mockVectorSearcher struct {
	nodes []RankedNode
	err   error
	delay time.Duration
}

func (m *mockVectorSearcher) Search(ctx context.Context, vector []float32, repo string, limit int) ([]RankedNode, error) {
	if m.delay > 0 {
		select {
		case <-time.After(m.delay):
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
	return m.nodes, m.err
}

type mockGraphSearcher struct {
	nodes []RankedNode
	err   error
	delay time.Duration
}

func (m *mockGraphSearcher) Search(ctx context.Context, keywords []string, symbols []string, repo string, limit int) ([]RankedNode, error) {
	if m.delay > 0 {
		select {
		case <-time.After(m.delay):
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
	return m.nodes, m.err
}

// --- tests ---

func TestRetriever_BothPaths(t *testing.T) {
	r := &Retriever{
		embedder: &mockEmbedder{vector: []float32{0.1, 0.2}},
		vector: &mockVectorSearcher{nodes: []RankedNode{
			{StableID: "a", Name: "funcA", Type: "function", Source: "vector"},
		}},
		graph: &mockGraphSearcher{nodes: []RankedNode{
			{StableID: "a", Name: "funcA", Type: "function", Source: "graph"},
			{StableID: "b", Name: "funcB", Type: "function", Source: "graph"},
		}},
	}

	result, err := r.Retrieve(context.Background(), "test query", []string{"test"}, []string{"funcA"}, "repo", 8)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(result) == 0 {
		t.Fatal("expected results")
	}
	if result[0].RetrievalMethod != "both" {
		t.Errorf("expected retrieval_method='both', got %q", result[0].RetrievalMethod)
	}
}

func TestRetriever_TimeoutPartialResults(t *testing.T) {
	r := &Retriever{
		embedder: &mockEmbedder{vector: []float32{0.1}, delay: 2 * time.Second},
		vector:   &mockVectorSearcher{nodes: []RankedNode{{StableID: "a", Name: "a", Type: "function", Source: "vector"}}},
		graph: &mockGraphSearcher{nodes: []RankedNode{
			{StableID: "b", Name: "funcB", Type: "function", Source: "graph"},
		}},
	}

	start := time.Now()
	result, err := r.Retrieve(context.Background(), "test", []string{"test"}, nil, "repo", 8)
	elapsed := time.Since(start)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(result) != 1 {
		t.Fatalf("expected 1 result (graph only), got %d", len(result))
	}
	if result[0].StableId != "b" {
		t.Errorf("expected 'b' from graph, got %q", result[0].StableId)
	}
	// Should complete in ~500ms, not 2s (generous threshold for slow CI)
	if elapsed > 1500*time.Millisecond {
		t.Errorf("expected timeout at ~500ms, took %v", elapsed)
	}
}

func TestRetriever_BothFail(t *testing.T) {
	r := &Retriever{
		embedder: &mockEmbedder{err: errors.New("embed fail")},
		vector:   &mockVectorSearcher{err: errors.New("qdrant fail")},
		graph:    &mockGraphSearcher{err: errors.New("neo4j fail")},
	}

	result, err := r.Retrieve(context.Background(), "test", []string{"test"}, nil, "repo", 8)
	if err != nil {
		t.Fatalf("expected graceful degradation (no error), got: %v", err)
	}
	if len(result) != 0 {
		t.Fatalf("expected 0 results, got %d", len(result))
	}
}

func TestRetriever_EmptyEmbedQuery_SkipsVector(t *testing.T) {
	r := &Retriever{
		embedder: &mockEmbedder{err: errors.New("should not be called")},
		vector:   &mockVectorSearcher{err: errors.New("should not be called")},
		graph: &mockGraphSearcher{nodes: []RankedNode{
			{StableID: "a", Name: "funcA", Type: "function", Source: "graph"},
		}},
	}

	result, err := r.Retrieve(context.Background(), "", []string{"test"}, nil, "repo", 8)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(result) != 1 {
		t.Fatalf("expected 1 graph result, got %d", len(result))
	}
}

func TestRetriever_EmptyKeywordsAndSymbols_SkipsGraph(t *testing.T) {
	r := &Retriever{
		embedder: &mockEmbedder{vector: []float32{0.1}},
		vector: &mockVectorSearcher{nodes: []RankedNode{
			{StableID: "a", Name: "funcA", Type: "function", Source: "vector"},
		}},
		graph: &mockGraphSearcher{err: errors.New("should not be called")},
	}

	result, err := r.Retrieve(context.Background(), "test", nil, nil, "repo", 8)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(result) != 1 {
		t.Fatalf("expected 1 vector result, got %d", len(result))
	}
}
