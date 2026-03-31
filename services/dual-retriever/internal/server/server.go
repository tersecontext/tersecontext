package server

import (
	"context"

	querypb "github.com/tersecontext/tc/services/dual-retriever/gen"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type RetrieverService interface {
	Retrieve(ctx context.Context, embedQuery string, keywords, symbols []string, repo string, maxSeeds int) ([]*querypb.SeedNode, error)
}

type QueryServer struct {
	querypb.UnimplementedQueryServiceServer
	retriever RetrieverService
}

func NewQueryServer(retriever RetrieverService) *QueryServer {
	return &QueryServer{retriever: retriever}
}

func (s *QueryServer) Retrieve(ctx context.Context, req *querypb.RetrieveRequest) (*querypb.SeedNodesResponse, error) {
	intent := req.GetIntent()
	maxSeeds := int(req.GetMaxSeeds())
	if maxSeeds <= 0 {
		maxSeeds = 8
	}

	nodes, err := s.retriever.Retrieve(
		ctx,
		intent.GetEmbedQuery(),
		intent.GetKeywords(),
		intent.GetSymbols(),
		req.GetRepo(),
		maxSeeds,
	)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "retrieve: %v", err)
	}

	return &querypb.SeedNodesResponse{Nodes: nodes}, nil
}

func (s *QueryServer) Understand(ctx context.Context, req *querypb.UnderstandRequest) (*querypb.QueryIntentResponse, error) {
	return nil, status.Error(codes.Unimplemented, "Understand not implemented by dual-retriever")
}

func (s *QueryServer) Expand(ctx context.Context, req *querypb.ExpandRequest) (*querypb.RankedSubgraphResponse, error) {
	return nil, status.Error(codes.Unimplemented, "Expand not implemented by dual-retriever")
}

func (s *QueryServer) Serialize(ctx context.Context, req *querypb.SerializeRequest) (*querypb.ContextDocResponse, error) {
	return nil, status.Error(codes.Unimplemented, "Serialize not implemented by dual-retriever")
}
