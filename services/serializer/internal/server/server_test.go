package server_test

import (
	"context"
	"testing"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/server"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func TestSerialize_success(t *testing.T) {
	srv := server.NewQueryServer()
	req := &querypb.SerializeRequest{
		Subgraph: &querypb.RankedSubgraphResponse{
			Nodes: []*querypb.SubgraphNode{
				{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0,
					Signature: "foo()", Name: "foo"},
			},
		},
		Intent: &querypb.QueryIntentResponse{RawQuery: "what is foo"},
		Repo:   "test",
	}
	resp, err := srv.Serialize(context.Background(), req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resp.Document == "" {
		t.Error("expected non-empty document")
	}
	if resp.TokenCount <= 0 {
		t.Error("expected positive token count")
	}
}

func TestSerialize_nilSubgraph(t *testing.T) {
	srv := server.NewQueryServer()
	req := &querypb.SerializeRequest{
		Intent: &querypb.QueryIntentResponse{RawQuery: "q"},
		Repo:   "r",
	}
	_, err := srv.Serialize(context.Background(), req)
	if err == nil {
		t.Fatal("expected error for nil subgraph")
	}
	if status.Code(err) != codes.InvalidArgument {
		t.Errorf("expected InvalidArgument, got %v", status.Code(err))
	}
}

func TestUnimplementedMethods(t *testing.T) {
	srv := server.NewQueryServer()
	ctx := context.Background()
	if _, err := srv.Understand(ctx, &querypb.UnderstandRequest{}); status.Code(err) != codes.Unimplemented {
		t.Errorf("Understand: expected Unimplemented, got %v", err)
	}
	if _, err := srv.Retrieve(ctx, &querypb.RetrieveRequest{}); status.Code(err) != codes.Unimplemented {
		t.Errorf("Retrieve: expected Unimplemented, got %v", err)
	}
	if _, err := srv.Expand(ctx, &querypb.ExpandRequest{}); status.Code(err) != codes.Unimplemented {
		t.Errorf("Expand: expected Unimplemented, got %v", err)
	}
}
