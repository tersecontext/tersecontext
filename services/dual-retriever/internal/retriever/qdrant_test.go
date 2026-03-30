package retriever

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHTTPEmbedder_Embed(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/embed" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		if r.Method != http.MethodPost {
			t.Errorf("unexpected method: %s", r.Method)
		}
		var body struct{ Text string `json:"text"` }
		json.NewDecoder(r.Body).Decode(&body)
		if body.Text != "test query" {
			t.Errorf("unexpected text: %s", body.Text)
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"vector": []float64{0.1, 0.2, 0.3},
		})
	}))
	defer server.Close()

	embedder := NewHTTPEmbedder(server.URL, server.Client())
	vec, err := embedder.Embed(context.Background(), "test query")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(vec) != 3 {
		t.Fatalf("expected 3-dim vector, got %d", len(vec))
	}
}

func TestHTTPEmbedder_Error(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer server.Close()

	embedder := NewHTTPEmbedder(server.URL, server.Client())
	_, err := embedder.Embed(context.Background(), "test")
	if err == nil {
		t.Fatal("expected error for 500 response")
	}
}

func TestQdrantSearcher_Search(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/collections/nodes/points/search" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		var body map[string]interface{}
		json.NewDecoder(r.Body).Decode(&body)
		filter := body["filter"].(map[string]interface{})
		must := filter["must"].([]interface{})
		if len(must) != 1 {
			t.Fatalf("expected 1 filter condition, got %d", len(must))
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"result": []map[string]interface{}{
				{
					"id":      1,
					"score":   0.95,
					"payload": map[string]interface{}{"stable_id": "abc", "name": "funcA", "type": "function"},
				},
				{
					"id":      2,
					"score":   0.80,
					"payload": map[string]interface{}{"stable_id": "def", "name": "funcB", "type": "function"},
				},
			},
		})
	}))
	defer server.Close()

	searcher := NewQdrantSearcher(server.URL, "nodes", server.Client())
	nodes, err := searcher.Search(context.Background(), []float32{0.1, 0.2}, "test-repo", 10)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(nodes) != 2 {
		t.Fatalf("expected 2 nodes, got %d", len(nodes))
	}
	if nodes[0].StableID != "abc" {
		t.Errorf("expected stable_id='abc', got %q", nodes[0].StableID)
	}
	if nodes[0].Source != "vector" {
		t.Errorf("expected source='vector', got %q", nodes[0].Source)
	}
}

func TestQdrantSearcher_Error(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer server.Close()

	searcher := NewQdrantSearcher(server.URL, "nodes", server.Client())
	_, err := searcher.Search(context.Background(), []float32{0.1}, "repo", 10)
	if err == nil {
		t.Fatal("expected error for 500 response")
	}
}
