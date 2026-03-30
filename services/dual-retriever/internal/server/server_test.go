package server

import (
	"context"
	"testing"

	querypb "github.com/tersecontext/tc/services/dual-retriever/gen"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type mockRetriever struct {
	nodes []*querypb.SeedNode
	err   error
}

func (m *mockRetriever) Retrieve(ctx context.Context, embedQuery string, keywords, symbols []string, repo string, maxSeeds int) ([]*querypb.SeedNode, error) {
	return m.nodes, m.err
}

func TestServer_Retrieve(t *testing.T) {
	srv := NewQueryServer(&mockRetriever{
		nodes: []*querypb.SeedNode{
			{StableId: "a", Name: "funcA", Type: "function", Score: 0.5, RetrievalMethod: "both"},
		},
	})

	resp, err := srv.Retrieve(context.Background(), &querypb.RetrieveRequest{
		Intent: &querypb.QueryIntentResponse{
			EmbedQuery: "test",
			Keywords:   []string{"test"},
		},
		Repo:     "test-repo",
		MaxSeeds: 8,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(resp.Nodes) != 1 {
		t.Fatalf("expected 1 node, got %d", len(resp.Nodes))
	}
	if resp.Nodes[0].StableId != "a" {
		t.Errorf("expected stable_id='a', got %q", resp.Nodes[0].StableId)
	}
}

func TestServer_Retrieve_DefaultMaxSeeds(t *testing.T) {
	capture := &captureRetriever{}
	srv := NewQueryServer(capture)

	_, _ = srv.Retrieve(context.Background(), &querypb.RetrieveRequest{
		Intent: &querypb.QueryIntentResponse{EmbedQuery: "test"},
		Repo:   "repo",
		// MaxSeeds = 0 (should default to 8)
	})
	if !capture.called {
		t.Fatal("retriever was not called")
	}
	if capture.maxSeeds != 8 {
		t.Errorf("expected default maxSeeds=8, got %d", capture.maxSeeds)
	}
}

type captureRetriever struct {
	maxSeeds int
	called   bool
}

func (c *captureRetriever) Retrieve(ctx context.Context, embedQuery string, keywords, symbols []string, repo string, maxSeeds int) ([]*querypb.SeedNode, error) {
	c.called = true
	c.maxSeeds = maxSeeds
	return nil, nil
}

func TestServer_Understand_Unimplemented(t *testing.T) {
	srv := NewQueryServer(&mockRetriever{})
	_, err := srv.Understand(context.Background(), &querypb.UnderstandRequest{})
	if status.Code(err) != codes.Unimplemented {
		t.Errorf("expected Unimplemented, got %v", err)
	}
}
