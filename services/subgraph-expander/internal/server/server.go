package server

import (
	"context"
	"errors"

	querypb "github.com/tersecontext/tc/services/subgraph-expander/gen"
	"github.com/tersecontext/tc/services/subgraph-expander/internal/expander"
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
