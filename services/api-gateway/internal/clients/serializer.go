// services/api-gateway/internal/clients/serializer.go
package clients

import (
	"context"

	querypb "github.com/tersecontext/tc/services/api-gateway/gen"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
)

// SerializerClient calls the serializer gRPC service.
type SerializerClient struct {
	conn   *grpc.ClientConn
	client querypb.QueryServiceClient
}

// NewSerializerClient dials addr (e.g. "serializer:8089").
func NewSerializerClient(addr string) (*SerializerClient, error) {
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, err
	}
	return &SerializerClient{conn: conn, client: querypb.NewQueryServiceClient(conn)}, nil
}

// Serialize calls QueryService.Serialize (unary — returns the full document as a string).
func (c *SerializerClient) Serialize(ctx context.Context, subgraph *querypb.RankedSubgraphResponse, intent *querypb.QueryIntentResponse, repo string, traceID string) (*querypb.ContextDocResponse, error) {
	ctx = metadata.AppendToOutgoingContext(ctx, "x-trace-id", traceID)
	return c.client.Serialize(ctx, &querypb.SerializeRequest{
		Subgraph: subgraph,
		Intent:   intent,
		Repo:     repo,
	})
}
