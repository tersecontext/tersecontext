package postgres

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"
)

// Client wraps a pgx connection pool.
type Client struct {
	pool *pgxpool.Pool
}

// New creates a Client connected to the given DSN.
func New(ctx context.Context, dsn string) (*Client, error) {
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, fmt.Errorf("postgres: open pool: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("postgres: ping: %w", err)
	}
	return &Client{pool: pool}, nil
}

// Close closes the connection pool.
func (c *Client) Close() {
	c.pool.Close()
}
