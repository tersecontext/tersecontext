// services/api-gateway/internal/clients/retriever.go
package clients

import (
	"context"

	querypb "github.com/tersecontext/tc/services/api-gateway/gen"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
)

// RetrieverClient calls the dual-retriever gRPC service.
type RetrieverClient struct {
	client querypb.QueryServiceClient
}

// NewRetrieverClient dials addr (e.g. "dual-retriever:8087").
func NewRetrieverClient(addr string) (*RetrieverClient, error) {
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, err
	}
	return &RetrieverClient{client: querypb.NewQueryServiceClient(conn)}, nil
}

// Retrieve calls QueryService.Retrieve with traceID propagated as gRPC metadata.
func (c *RetrieverClient) Retrieve(ctx context.Context, intent *querypb.QueryIntentResponse, repo string, maxSeeds int32, traceID string) (*querypb.SeedNodesResponse, error) {
	ctx = metadata.AppendToOutgoingContext(ctx, "x-trace-id", traceID)
	return c.client.Retrieve(ctx, &querypb.RetrieveRequest{
		Intent:   intent,
		Repo:     repo,
		MaxSeeds: maxSeeds,
	})
}
