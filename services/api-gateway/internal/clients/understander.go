// services/api-gateway/internal/clients/understander.go
package clients

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	querypb "github.com/tersecontext/tc/services/api-gateway/gen"
)

type understandRequest struct {
	Question string `json:"question"`
	Repo     string `json:"repo"`
}

type understandResponse struct {
	RawQuery   string   `json:"raw_query"`
	Keywords   []string `json:"keywords"`
	Symbols    []string `json:"symbols"`
	QueryType  string   `json:"query_type"`
	EmbedQuery string   `json:"embed_query"`
	Scope      string   `json:"scope"`
}

// UnderstanderClient calls the query-understander HTTP service.
type UnderstanderClient struct {
	baseURL    string
	httpClient *http.Client
}

// NewUnderstanderClient creates a client targeting baseURL (e.g. "http://query-understander:8080").
func NewUnderstanderClient(baseURL string) *UnderstanderClient {
	return &UnderstanderClient{
		baseURL:    baseURL,
		httpClient: &http.Client{Timeout: 30 * time.Second},
	}
}

// Understand calls POST /understand and maps the response to a proto QueryIntentResponse.
func (c *UnderstanderClient) Understand(ctx context.Context, question, repo, traceID string) (*querypb.QueryIntentResponse, error) {
	payload, err := json.Marshal(understandRequest{Question: question, Repo: repo})
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, "POST", c.baseURL+"/understand", bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Trace-Id", traceID)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("understander returned %d", resp.StatusCode)
	}

	var r understandResponse
	if err := json.NewDecoder(resp.Body).Decode(&r); err != nil {
		return nil, err
	}

	return &querypb.QueryIntentResponse{
		RawQuery:   r.RawQuery,
		Keywords:   r.Keywords,
		Symbols:    r.Symbols,
		QueryType:  r.QueryType,
		EmbedQuery: r.EmbedQuery,
		Scope:      r.Scope,
	}, nil
}
