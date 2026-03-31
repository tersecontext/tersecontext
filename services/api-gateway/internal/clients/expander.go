// services/api-gateway/internal/clients/expander.go
package clients

import (
	"context"

	querypb "github.com/tersecontext/tc/services/api-gateway/gen"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
)

// ExpanderClient calls the subgraph-expander gRPC service.
type ExpanderClient struct {
	conn   *grpc.ClientConn
	client querypb.QueryServiceClient
}

// NewExpanderClient dials addr (e.g. "subgraph-expander:8088").
func NewExpanderClient(addr string) (*ExpanderClient, error) {
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, err
	}
	return &ExpanderClient{conn: conn, client: querypb.NewQueryServiceClient(conn)}, nil
}

// Expand calls QueryService.Expand.
func (c *ExpanderClient) Expand(ctx context.Context, seeds *querypb.SeedNodesResponse, intent *querypb.QueryIntentResponse, maxTokens int32, traceID string) (*querypb.RankedSubgraphResponse, error) {
	ctx = metadata.AppendToOutgoingContext(ctx, "x-trace-id", traceID)
	return c.client.Expand(ctx, &querypb.ExpandRequest{
		Seeds:     seeds,
		Intent:    intent,
		MaxTokens: maxTokens,
	})
}
