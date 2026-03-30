package retriever

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
)

// Embedder converts text into a vector embedding.
type Embedder interface {
	Embed(ctx context.Context, text string) ([]float32, error)
}

// VectorSearcher searches a vector store and returns ranked nodes.
type VectorSearcher interface {
	Search(ctx context.Context, vector []float32, repo string, limit int) ([]RankedNode, error)
}

// HTTPEmbedder calls an external embedder service over HTTP.
type HTTPEmbedder struct {
	url    string
	client *http.Client
}

// NewHTTPEmbedder creates an HTTPEmbedder targeting the given base URL.
func NewHTTPEmbedder(url string, client *http.Client) *HTTPEmbedder {
	if client == nil {
		client = &http.Client{}
	}
	return &HTTPEmbedder{url: url, client: client}
}

// Embed sends text to the embedder service and returns the resulting vector.
func (e *HTTPEmbedder) Embed(ctx context.Context, text string) ([]float32, error) {
	body, _ := json.Marshal(map[string]string{"text": text})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, e.url+"/embed", bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("create embed request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := e.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("embed request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("embed request returned %d", resp.StatusCode)
	}

	var result struct {
		Vector []float32 `json:"vector"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decode embed response: %w", err)
	}
	return result.Vector, nil
}

// QdrantSearcher searches a Qdrant collection via its REST API.
type QdrantSearcher struct {
	url        string
	collection string
	client     *http.Client
}

// NewQdrantSearcher creates a QdrantSearcher targeting the given Qdrant base URL
// and collection name.
func NewQdrantSearcher(url string, collection string, client *http.Client) *QdrantSearcher {
	if client == nil {
		client = &http.Client{}
	}
	return &QdrantSearcher{url: url, collection: collection, client: client}
}

// Search performs a vector similarity search in Qdrant filtered by repo,
// returning up to limit ranked nodes.
func (q *QdrantSearcher) Search(ctx context.Context, vector []float32, repo string, limit int) ([]RankedNode, error) {
	reqBody := map[string]interface{}{
		"vector":       vector,
		"limit":        limit,
		"with_payload": true,
		"filter": map[string]interface{}{
			"must": []map[string]interface{}{
				{
					"key": "repo",
					"match": map[string]interface{}{
						"value": repo,
					},
				},
			},
		},
	}

	body, _ := json.Marshal(reqBody)
	endpoint := fmt.Sprintf("%s/collections/%s/points/search", q.url, q.collection)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("create qdrant request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := q.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("qdrant search request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("qdrant search returned %d", resp.StatusCode)
	}

	var result struct {
		Result []struct {
			Payload map[string]interface{} `json:"payload"`
		} `json:"result"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decode qdrant response: %w", err)
	}

	nodes := make([]RankedNode, 0, len(result.Result))
	for _, point := range result.Result {
		nodes = append(nodes, RankedNode{
			StableID: fmt.Sprint(point.Payload["stable_id"]),
			Name:     fmt.Sprint(point.Payload["name"]),
			Type:     fmt.Sprint(point.Payload["type"]),
			Source:   "vector",
		})
	}
	return nodes, nil
}
