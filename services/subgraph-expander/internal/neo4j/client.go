package neo4j

import (
	"context"
	"fmt"

	"github.com/neo4j/neo4j-go-driver/v5/neo4j"
)

// Client wraps a Neo4j driver.
type Client struct {
	driver neo4j.DriverWithContext
}

// New creates a Client and verifies connectivity.
func New(ctx context.Context, uri, user, password string) (*Client, error) {
	driver, err := neo4j.NewDriverWithContext(uri, neo4j.BasicAuth(user, password, ""))
	if err != nil {
		return nil, fmt.Errorf("neo4j: create driver: %w", err)
	}
	if err := driver.VerifyConnectivity(ctx); err != nil {
		driver.Close(ctx)
		return nil, fmt.Errorf("neo4j: verify connectivity: %w", err)
	}
	return &Client{driver: driver}, nil
}

// Close closes the driver.
func (c *Client) Close(ctx context.Context) {
	c.driver.Close(ctx)
}
