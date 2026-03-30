package server

import (
	"context"
	"fmt"
	"testing"

	querypb "github.com/tersecontext/tc/services/subgraph-expander/gen"
	"github.com/tersecontext/tc/services/subgraph-expander/internal/expander"
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
		Seeds:     &querypb.SeedNodesResponse{Nodes: []*querypb.SeedNode{{StableId: "a", Score: 1.0}}},
		Intent:    &querypb.QueryIntentResponse{QueryType: "flow"},
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
