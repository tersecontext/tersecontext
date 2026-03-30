package server

import (
	"context"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type QueryServer struct {
	querypb.UnimplementedQueryServiceServer
	ser *serializer.Serializer
}

func NewQueryServer() *QueryServer {
	return &QueryServer{ser: serializer.New()}
}

func (s *QueryServer) Serialize(ctx context.Context, req *querypb.SerializeRequest) (*querypb.ContextDocResponse, error) {
	if req.GetSubgraph() == nil {
		return nil, status.Error(codes.InvalidArgument, "subgraph is required")
	}
	doc, tokens, err := s.ser.Serialize(req)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "serialize: %v", err)
	}
	return &querypb.ContextDocResponse{Document: doc, TokenCount: tokens}, nil
}

func (s *QueryServer) Understand(_ context.Context, _ *querypb.UnderstandRequest) (*querypb.QueryIntentResponse, error) {
	return nil, status.Error(codes.Unimplemented, "Understand not implemented by serializer")
}

func (s *QueryServer) Retrieve(_ context.Context, _ *querypb.RetrieveRequest) (*querypb.SeedNodesResponse, error) {
	return nil, status.Error(codes.Unimplemented, "Retrieve not implemented by serializer")
}

func (s *QueryServer) Expand(_ context.Context, _ *querypb.ExpandRequest) (*querypb.RankedSubgraphResponse, error) {
	return nil, status.Error(codes.Unimplemented, "Expand not implemented by serializer")
}
