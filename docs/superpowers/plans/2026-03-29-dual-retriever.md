# Dual Retriever Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the dual-retriever gRPC service that fans out to Qdrant (semantic) and Neo4j (keyword/symbol) in parallel, merges results with Reciprocal Rank Fusion, and returns ranked seed nodes.

**Architecture:** A gRPC server implements `QueryService/Retrieve`. The `Retriever` orchestrator fans out to `VectorSearcher` (Qdrant) and `GraphSearcher` (Neo4j) via goroutines with a 500ms timeout. Results are merged by a pure RRF function. Three interfaces (`Embedder`, `VectorSearcher`, `GraphSearcher`) enable unit testing with mocks.

**Tech Stack:** Go 1.23, gRPC, protobuf, neo4j-go-driver/v5, net/http (Qdrant REST API), log/slog

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `proto/query.proto` | Modify | Add `repo` and `max_seeds` to `RetrieveRequest` |
| `services/dual-retriever/gen/` | Generate | Proto stubs via `make proto` |
| `services/dual-retriever/internal/retriever/rrf.go` | Create | `RankedNode` type, `RRF` pure function |
| `services/dual-retriever/internal/retriever/rrf_test.go` | Create | RRF unit tests |
| `services/dual-retriever/internal/retriever/qdrant.go` | Create | `Embedder` + `VectorSearcher` interfaces, HTTP implementations (Qdrant REST API on port 6333) |
| `services/dual-retriever/internal/retriever/qdrant_test.go` | Create | Embedder + Qdrant client unit tests with httptest servers |
| `services/dual-retriever/internal/retriever/neo4j.go` | Create | `GraphSearcher` interface and Neo4j implementation |
| `services/dual-retriever/internal/retriever/neo4j_test.go` | Create | Neo4j client unit tests with mock driver |
| `services/dual-retriever/internal/retriever/retriever.go` | Create | `Retriever` orchestrator — parallel fan-out, timeout, RRF merge |
| `services/dual-retriever/internal/retriever/retriever_test.go` | Create | Orchestrator tests with mock interfaces |
| `services/dual-retriever/internal/server/server.go` | Create | gRPC `QueryService` implementation |
| `services/dual-retriever/internal/server/server_test.go` | Create | Server tests with mock retriever |
| `services/dual-retriever/cmd/main.go` | Create | Entry point — config, clients, gRPC + HTTP servers, shutdown |
| `services/dual-retriever/Dockerfile` | Create | Multi-stage Go build |
| `docker-compose.yml` | Modify | Add `dual-retriever` service |

---

## Task 1: Update proto and generate stubs

**Files:**
- Modify: `proto/query.proto`
- Generate: `services/dual-retriever/gen/`

- [ ] **Step 1: Add `repo` and `max_seeds` to `RetrieveRequest`**

In `proto/query.proto`, replace the `RetrieveRequest` message (lines 24-26):

```protobuf
message RetrieveRequest {
  QueryIntentResponse intent = 1;
  string repo = 2;
  int32 max_seeds = 3;
}
```

- [ ] **Step 2: Generate Go stubs**

Run: `make proto`

Expected: `services/dual-retriever/gen/` created with `query.pb.go` and `query_grpc.pb.go`.

- [ ] **Step 3: Add generated proto dependencies to go.mod**

Run:
```bash
cd services/dual-retriever
go get google.golang.org/grpc
go get google.golang.org/protobuf
go mod tidy
```

Expected: `go.mod` updated with grpc and protobuf dependencies. The `gen/` package should now compile.

- [ ] **Step 4: Verify generated code compiles**

Run: `cd services/dual-retriever && go build ./gen/...`

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add proto/query.proto services/dual-retriever/gen/ services/dual-retriever/go.mod services/dual-retriever/go.sum
git commit -m "feat(dual-retriever): add repo and max_seeds to RetrieveRequest, generate stubs"
```

---

## Task 2: RRF — pure function with tests

**Files:**
- Create: `services/dual-retriever/internal/retriever/rrf.go`
- Create: `services/dual-retriever/internal/retriever/rrf_test.go`

- [ ] **Step 1: Write the RRF tests**

Create `services/dual-retriever/internal/retriever/rrf_test.go`:

```go
package retriever

import (
	"testing"
)

func TestRRF_BothLists(t *testing.T) {
	vector := []RankedNode{
		{StableID: "a", Name: "funcA", Type: "function", Source: "vector"},
		{StableID: "b", Name: "funcB", Type: "function", Source: "vector"},
		{StableID: "c", Name: "funcC", Type: "function", Source: "vector"},
	}
	graph := []RankedNode{
		{StableID: "b", Name: "funcB", Type: "function", Source: "graph"},
		{StableID: "d", Name: "funcD", Type: "function", Source: "graph"},
		{StableID: "a", Name: "funcA", Type: "function", Source: "graph"},
	}

	result := RRF([][]RankedNode{vector, graph}, 60, 10)

	// "b" is rank 1 in vector (score 1/62) and rank 0 in graph (score 1/61)
	// "a" is rank 0 in vector (score 1/61) and rank 2 in graph (score 1/63)
	// Both "a" and "b" appear in both lists, but "b" has higher combined score
	if len(result) < 3 {
		t.Fatalf("expected at least 3 results, got %d", len(result))
	}

	// b should rank first (highest combined RRF score)
	if result[0].StableId != "b" {
		t.Errorf("expected first result to be 'b', got %q", result[0].StableId)
	}
	// a should rank second
	if result[1].StableId != "a" {
		t.Errorf("expected second result to be 'a', got %q", result[1].StableId)
	}

	// Check retrieval_method
	methods := map[string]string{}
	for _, n := range result {
		methods[n.StableId] = n.RetrievalMethod
	}
	if methods["a"] != "both" {
		t.Errorf("expected 'a' retrieval_method='both', got %q", methods["a"])
	}
	if methods["b"] != "both" {
		t.Errorf("expected 'b' retrieval_method='both', got %q", methods["b"])
	}
	if methods["c"] != "vector" {
		t.Errorf("expected 'c' retrieval_method='vector', got %q", methods["c"])
	}
	if methods["d"] != "graph" {
		t.Errorf("expected 'd' retrieval_method='graph', got %q", methods["d"])
	}
}

func TestRRF_SingleList(t *testing.T) {
	vector := []RankedNode{
		{StableID: "a", Name: "funcA", Type: "function", Source: "vector"},
		{StableID: "b", Name: "funcB", Type: "function", Source: "vector"},
	}

	result := RRF([][]RankedNode{vector}, 60, 10)
	if len(result) != 2 {
		t.Fatalf("expected 2 results, got %d", len(result))
	}
	if result[0].RetrievalMethod != "vector" {
		t.Errorf("expected retrieval_method='vector', got %q", result[0].RetrievalMethod)
	}
}

func TestRRF_EmptyLists(t *testing.T) {
	result := RRF([][]RankedNode{}, 60, 10)
	if len(result) != 0 {
		t.Fatalf("expected 0 results, got %d", len(result))
	}

	result = RRF([][]RankedNode{{}, {}}, 60, 10)
	if len(result) != 0 {
		t.Fatalf("expected 0 results for empty sublists, got %d", len(result))
	}
}

func TestRRF_MaxSeedsCap(t *testing.T) {
	nodes := make([]RankedNode, 20)
	for i := range nodes {
		nodes[i] = RankedNode{
			StableID: string(rune('a' + i)),
			Name:     string(rune('a' + i)),
			Type:     "function",
			Source:   "vector",
		}
	}

	result := RRF([][]RankedNode{nodes}, 60, 5)
	if len(result) != 5 {
		t.Fatalf("expected 5 results (capped), got %d", len(result))
	}
}

func TestRRF_BothRanksHigherThanSingle(t *testing.T) {
	// "shared" appears in both lists, "vector_only" only in vector
	// Even though "vector_only" is rank 0 in vector, "shared" should score higher
	// because it gets contributions from both lists
	vector := []RankedNode{
		{StableID: "vector_only", Name: "vo", Type: "function", Source: "vector"},
		{StableID: "shared", Name: "sh", Type: "function", Source: "vector"},
	}
	graph := []RankedNode{
		{StableID: "shared", Name: "sh", Type: "function", Source: "graph"},
	}

	result := RRF([][]RankedNode{vector, graph}, 60, 10)

	if result[0].StableId != "shared" {
		t.Errorf("expected 'shared' to rank first (both lists), got %q", result[0].StableId)
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/dual-retriever && go test ./internal/retriever/ -v -run TestRRF`

Expected: compilation error — `RankedNode`, `RRF` not defined.

- [ ] **Step 3: Implement RRF**

Create `services/dual-retriever/internal/retriever/rrf.go`:

```go
package retriever

import (
	"sort"

	querypb "github.com/tersecontext/tc/services/dual-retriever/gen"
)

const rrfK = 60

// RankedNode is the internal result type from each retrieval path.
type RankedNode struct {
	StableID string
	Name     string
	Type     string
	Source   string // "vector" or "graph"
}

// RRF merges multiple ranked lists using Reciprocal Rank Fusion.
// k is the RRF constant (use rrfK=60). maxSeeds caps the output length.
func RRF(lists [][]RankedNode, k int, maxSeeds int) []*querypb.SeedNode {
	type entry struct {
		score   float64
		name    string
		typ     string
		sources map[string]bool
	}

	entries := map[string]*entry{}

	for _, list := range lists {
		for rank, node := range list {
			e, ok := entries[node.StableID]
			if !ok {
				e = &entry{
					name:    node.Name,
					typ:     node.Type,
					sources: map[string]bool{},
				}
				entries[node.StableID] = e
			}
			e.score += 1.0 / float64(k+rank+1)
			e.sources[node.Source] = true
		}
	}

	result := make([]*querypb.SeedNode, 0, len(entries))
	for id, e := range entries {
		method := ""
		if e.sources["vector"] && e.sources["graph"] {
			method = "both"
		} else if e.sources["vector"] {
			method = "vector"
		} else {
			method = "graph"
		}
		result = append(result, &querypb.SeedNode{
			StableId:        id,
			Name:            e.name,
			Type:            e.typ,
			Score:           e.score,
			RetrievalMethod: method,
		})
	}

	sort.Slice(result, func(i, j int) bool {
		return result[i].Score > result[j].Score
	})

	if len(result) > maxSeeds {
		result = result[:maxSeeds]
	}
	return result
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/dual-retriever && go test ./internal/retriever/ -v -run TestRRF`

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/dual-retriever/internal/retriever/rrf.go services/dual-retriever/internal/retriever/rrf_test.go
git commit -m "feat(dual-retriever): implement RRF with tests"
```

---

## Task 3: Qdrant client — embedder + vector search (REST API)

**Files:**
- Create: `services/dual-retriever/internal/retriever/qdrant.go`
- Create: `services/dual-retriever/internal/retriever/qdrant_test.go`

Uses Qdrant's REST API on port 6333 via `net/http` — no external Qdrant client dependency needed. The `qdrant/go-client` package uses gRPC on port 6334, which would require exposing an additional port and adds unnecessary complexity. REST is simpler and matches the `QDRANT_URL` env var.

- [ ] **Step 1: Write the embedder and Qdrant search tests**

Create `services/dual-retriever/internal/retriever/qdrant_test.go`:

```go
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

		// Verify filter includes repo
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/dual-retriever && go test ./internal/retriever/ -v -run "TestHTTPEmbedder|TestQdrantSearcher"`

Expected: compilation error — `NewHTTPEmbedder`, `NewQdrantSearcher` not defined.

- [ ] **Step 3: Implement embedder and Qdrant REST client**

Create `services/dual-retriever/internal/retriever/qdrant.go`:

```go
package retriever

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
)

// Embedder converts text to a vector.
type Embedder interface {
	Embed(ctx context.Context, text string) ([]float32, error)
}

// VectorSearcher searches a vector store.
type VectorSearcher interface {
	Search(ctx context.Context, vector []float32, repo string, limit int) ([]RankedNode, error)
}

// --- HTTPEmbedder ---

// HTTPEmbedder calls the embedder service over HTTP.
type HTTPEmbedder struct {
	url    string
	client *http.Client
}

func NewHTTPEmbedder(url string, client *http.Client) *HTTPEmbedder {
	if client == nil {
		client = &http.Client{}
	}
	return &HTTPEmbedder{url: url, client: client}
}

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

// --- QdrantSearcher (REST API) ---

// QdrantSearcher searches Qdrant via its REST API (port 6333).
type QdrantSearcher struct {
	url        string
	collection string
	client     *http.Client
}

func NewQdrantSearcher(url string, collection string, client *http.Client) *QdrantSearcher {
	if client == nil {
		client = &http.Client{}
	}
	return &QdrantSearcher{url: url, collection: collection, client: client}
}

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/dual-retriever && go test ./internal/retriever/ -v -run "TestHTTPEmbedder|TestQdrantSearcher"`

Expected: all 4 tests PASS. No external dependencies needed — pure `net/http`.

- [ ] **Step 5: Commit**

```bash
git add services/dual-retriever/internal/retriever/qdrant.go services/dual-retriever/internal/retriever/qdrant_test.go
git commit -m "feat(dual-retriever): add embedder and Qdrant REST vector search client"
```

---

## Task 4: Neo4j client — keyword + symbol search

**Files:**
- Create: `services/dual-retriever/internal/retriever/neo4j.go`
- Create: `services/dual-retriever/internal/retriever/neo4j_test.go`

- [ ] **Step 1: Write the query builder test**

Create `services/dual-retriever/internal/retriever/neo4j_test.go`:

```go
package retriever

import (
	"testing"
)

func TestBuildFullTextQuery(t *testing.T) {
	tests := []struct {
		name     string
		keywords []string
		symbols  []string
		want     string
	}{
		{
			name:     "symbols and keywords",
			keywords: []string{"auth", "login"},
			symbols:  []string{"authenticate", "AuthService"},
			want:     "authenticate OR AuthService OR auth OR login",
		},
		{
			name:     "keywords only",
			keywords: []string{"auth", "login"},
			symbols:  nil,
			want:     "auth OR login",
		},
		{
			name:     "symbols only",
			symbols:  []string{"AuthService"},
			keywords: nil,
			want:     "AuthService",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := buildFullTextQuery(tt.keywords, tt.symbols)
			if got != tt.want {
				t.Errorf("buildFullTextQuery() = %q, want %q", got, tt.want)
			}
		})
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/dual-retriever && go test ./internal/retriever/ -v -run TestBuildFullTextQuery`

Expected: compilation error — `buildFullTextQuery` not defined.

- [ ] **Step 3: Implement Neo4j client**

Create `services/dual-retriever/internal/retriever/neo4j.go`:

```go
package retriever

import (
	"context"
	"fmt"
	"strings"

	"github.com/neo4j/neo4j-go-driver/v5/neo4j"
)

// GraphSearcher searches a graph store.
type GraphSearcher interface {
	Search(ctx context.Context, query string, symbols []string, repo string, limit int) ([]RankedNode, error)
}

// Neo4jSearcher searches Neo4j using full-text index and direct name match.
type Neo4jSearcher struct {
	driver neo4j.DriverWithContext
}

func NewNeo4jSearcher(driver neo4j.DriverWithContext) *Neo4jSearcher {
	return &Neo4jSearcher{driver: driver}
}

func buildFullTextQuery(keywords, symbols []string) string {
	terms := make([]string, 0, len(symbols)+len(keywords))
	terms = append(terms, symbols...)
	terms = append(terms, keywords...)
	return strings.Join(terms, " OR ")
}

func (n *Neo4jSearcher) Search(ctx context.Context, query string, symbols []string, repo string, limit int) ([]RankedNode, error) {
	session := n.driver.NewSession(ctx, neo4j.SessionConfig{AccessMode: neo4j.AccessModeRead})
	defer session.Close(ctx)

	seen := map[string]bool{}
	var nodes []RankedNode

	// Direct name match for exact symbols (prepended — highest priority)
	if len(symbols) > 0 {
		directResult, err := session.Run(ctx,
			`MATCH (n:Node {repo: $repo, active: true})
			 WHERE n.name IN $symbols OR n.qualified_name IN $symbols
			 RETURN n.stable_id AS stable_id, n.name AS name, n.type AS type`,
			map[string]interface{}{"repo": repo, "symbols": symbols},
		)
		if err != nil {
			return nil, fmt.Errorf("neo4j direct match: %w", err)
		}
		for directResult.Next(ctx) {
			record := directResult.Record()
			stableID, _ := record.Get("stable_id")
			name, _ := record.Get("name")
			typ, _ := record.Get("type")
			sid := stableID.(string)
			if !seen[sid] {
				seen[sid] = true
				nodes = append(nodes, RankedNode{
					StableID: sid,
					Name:     name.(string),
					Type:     typ.(string),
					Source:   "graph",
				})
			}
		}
	}

	// Full-text search
	if query != "" {
		ftResult, err := session.Run(ctx,
			`CALL db.index.fulltext.queryNodes("node_search", $query)
			 YIELD node, score
			 WHERE node.repo = $repo AND node.active = true
			 RETURN node.stable_id AS stable_id, node.name AS name, node.type AS type, score
			 ORDER BY score DESC
			 LIMIT $limit`,
			map[string]interface{}{"query": query, "repo": repo, "limit": limit},
		)
		if err != nil {
			return nil, fmt.Errorf("neo4j fulltext search: %w", err)
		}
		for ftResult.Next(ctx) {
			record := ftResult.Record()
			stableID, _ := record.Get("stable_id")
			name, _ := record.Get("name")
			typ, _ := record.Get("type")
			sid := stableID.(string)
			if !seen[sid] {
				seen[sid] = true
				nodes = append(nodes, RankedNode{
					StableID: sid,
					Name:     name.(string),
					Type:     typ.(string),
					Source:   "graph",
				})
			}
		}
	}

	return nodes, nil
}
```

- [ ] **Step 4: Add neo4j dependency**

Run:
```bash
cd services/dual-retriever
go get github.com/neo4j/neo4j-go-driver/v5
go mod tidy
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd services/dual-retriever && go test ./internal/retriever/ -v -run TestBuildFullTextQuery`

Expected: all 3 subtests PASS.

- [ ] **Step 6: Commit**

```bash
git add services/dual-retriever/internal/retriever/neo4j.go services/dual-retriever/internal/retriever/neo4j_test.go services/dual-retriever/go.mod services/dual-retriever/go.sum
git commit -m "feat(dual-retriever): add Neo4j keyword and symbol search client"
```

---

## Task 5: Retriever orchestrator — parallel fan-out with timeout

**Files:**
- Create: `services/dual-retriever/internal/retriever/retriever.go`
- Create: `services/dual-retriever/internal/retriever/retriever_test.go`

- [ ] **Step 1: Write the orchestrator tests**

Create `services/dual-retriever/internal/retriever/retriever_test.go`:

```go
package retriever

import (
	"context"
	"errors"
	"testing"
	"time"
)

// --- mocks ---

type mockEmbedder struct {
	vector []float32
	err    error
	delay  time.Duration
}

func (m *mockEmbedder) Embed(ctx context.Context, text string) ([]float32, error) {
	if m.delay > 0 {
		select {
		case <-time.After(m.delay):
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
	return m.vector, m.err
}

type mockVectorSearcher struct {
	nodes []RankedNode
	err   error
	delay time.Duration
}

func (m *mockVectorSearcher) Search(ctx context.Context, vector []float32, repo string, limit int) ([]RankedNode, error) {
	if m.delay > 0 {
		select {
		case <-time.After(m.delay):
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
	return m.nodes, m.err
}

type mockGraphSearcher struct {
	nodes []RankedNode
	err   error
	delay time.Duration
}

func (m *mockGraphSearcher) Search(ctx context.Context, query string, symbols []string, repo string, limit int) ([]RankedNode, error) {
	if m.delay > 0 {
		select {
		case <-time.After(m.delay):
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
	return m.nodes, m.err
}

// --- tests ---

func TestRetriever_BothPaths(t *testing.T) {
	r := &Retriever{
		embedder: &mockEmbedder{vector: []float32{0.1, 0.2}},
		vector: &mockVectorSearcher{nodes: []RankedNode{
			{StableID: "a", Name: "funcA", Type: "function", Source: "vector"},
		}},
		graph: &mockGraphSearcher{nodes: []RankedNode{
			{StableID: "a", Name: "funcA", Type: "function", Source: "graph"},
			{StableID: "b", Name: "funcB", Type: "function", Source: "graph"},
		}},
	}

	result, err := r.Retrieve(context.Background(), "test query", []string{"test"}, []string{"funcA"}, "repo", 8)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(result) == 0 {
		t.Fatal("expected results")
	}
	// "a" should be "both" since it appears in both paths
	if result[0].RetrievalMethod != "both" {
		t.Errorf("expected retrieval_method='both', got %q", result[0].RetrievalMethod)
	}
}

func TestRetriever_TimeoutPartialResults(t *testing.T) {
	r := &Retriever{
		embedder: &mockEmbedder{vector: []float32{0.1}, delay: 2 * time.Second},
		vector:   &mockVectorSearcher{nodes: []RankedNode{{StableID: "a", Name: "a", Type: "function", Source: "vector"}}},
		graph: &mockGraphSearcher{nodes: []RankedNode{
			{StableID: "b", Name: "funcB", Type: "function", Source: "graph"},
		}},
	}

	start := time.Now()
	result, err := r.Retrieve(context.Background(), "test", []string{"test"}, nil, "repo", 8)
	elapsed := time.Since(start)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// Should return graph results only (vector timed out)
	if len(result) != 1 {
		t.Fatalf("expected 1 result (graph only), got %d", len(result))
	}
	if result[0].StableId != "b" {
		t.Errorf("expected 'b' from graph, got %q", result[0].StableId)
	}
	// Should complete in ~500ms, not 2s (generous threshold for slow CI)
	if elapsed > 1500*time.Millisecond {
		t.Errorf("expected timeout at ~500ms, took %v", elapsed)
	}
}

func TestRetriever_BothFail(t *testing.T) {
	r := &Retriever{
		embedder: &mockEmbedder{err: errors.New("embed fail")},
		vector:   &mockVectorSearcher{err: errors.New("qdrant fail")},
		graph:    &mockGraphSearcher{err: errors.New("neo4j fail")},
	}

	result, err := r.Retrieve(context.Background(), "test", []string{"test"}, nil, "repo", 8)
	if err != nil {
		t.Fatalf("expected graceful degradation (no error), got: %v", err)
	}
	if len(result) != 0 {
		t.Fatalf("expected 0 results, got %d", len(result))
	}
}

func TestRetriever_EmptyEmbedQuery_SkipsVector(t *testing.T) {
	r := &Retriever{
		embedder: &mockEmbedder{err: errors.New("should not be called")},
		vector:   &mockVectorSearcher{err: errors.New("should not be called")},
		graph: &mockGraphSearcher{nodes: []RankedNode{
			{StableID: "a", Name: "funcA", Type: "function", Source: "graph"},
		}},
	}

	result, err := r.Retrieve(context.Background(), "", []string{"test"}, nil, "repo", 8)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(result) != 1 {
		t.Fatalf("expected 1 graph result, got %d", len(result))
	}
}

func TestRetriever_EmptyKeywordsAndSymbols_SkipsGraph(t *testing.T) {
	r := &Retriever{
		embedder: &mockEmbedder{vector: []float32{0.1}},
		vector: &mockVectorSearcher{nodes: []RankedNode{
			{StableID: "a", Name: "funcA", Type: "function", Source: "vector"},
		}},
		graph: &mockGraphSearcher{err: errors.New("should not be called")},
	}

	result, err := r.Retrieve(context.Background(), "test", nil, nil, "repo", 8)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(result) != 1 {
		t.Fatalf("expected 1 vector result, got %d", len(result))
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/dual-retriever && go test ./internal/retriever/ -v -run TestRetriever`

Expected: compilation error — `Retriever` not defined.

- [ ] **Step 3: Implement the orchestrator**

Create `services/dual-retriever/internal/retriever/retriever.go`:

```go
package retriever

import (
	"context"
	"log/slog"
	"time"

	querypb "github.com/tersecontext/tc/services/dual-retriever/gen"
)

const (
	DefaultMaxSeeds    = 8
	retrievalTimeout   = 500 * time.Millisecond
)

// Retriever fans out to vector and graph search, merges with RRF.
type Retriever struct {
	embedder Embedder
	vector   VectorSearcher
	graph    GraphSearcher
}

func NewRetriever(embedder Embedder, vector VectorSearcher, graph GraphSearcher) *Retriever {
	return &Retriever{embedder: embedder, vector: vector, graph: graph}
}

type retrievalResult struct {
	nodes  []RankedNode
	source string
	err    error
}

func (r *Retriever) Retrieve(ctx context.Context, embedQuery string, keywords, symbols []string, repo string, maxSeeds int) ([]*querypb.SeedNode, error) {
	if maxSeeds <= 0 {
		maxSeeds = DefaultMaxSeeds
	}

	start := time.Now()
	limit := maxSeeds * 2

	vectorCh := make(chan retrievalResult, 1)
	graphCh := make(chan retrievalResult, 1)

	runVector := embedQuery != ""
	runGraph := len(keywords) > 0 || len(symbols) > 0

	// Launch vector path
	if runVector {
		go func() {
			vStart := time.Now()
			vec, err := r.embedder.Embed(ctx, embedQuery)
			if err != nil {
				slog.Warn("embed failed", "error", err, "vector_ms", time.Since(vStart).Milliseconds())
				vectorCh <- retrievalResult{source: "vector", err: err}
				return
			}
			nodes, err := r.vector.Search(ctx, vec, repo, limit)
			slog.Info("vector search complete", "vector_ms", time.Since(vStart).Milliseconds(), "vector_count", len(nodes))
			vectorCh <- retrievalResult{nodes: nodes, source: "vector", err: err}
		}()
	}

	// Launch graph path
	if runGraph {
		go func() {
			gStart := time.Now()
			query := buildFullTextQuery(keywords, symbols)
			nodes, err := r.graph.Search(ctx, query, symbols, repo, limit)
			slog.Info("graph search complete", "graph_ms", time.Since(gStart).Milliseconds(), "graph_count", len(nodes))
			graphCh <- retrievalResult{nodes: nodes, source: "graph", err: err}
		}()
	}

	// Collect results with timeout
	timer := time.NewTimer(retrievalTimeout)
	defer timer.Stop()

	expected := 0
	if runVector {
		expected++
	}
	if runGraph {
		expected++
	}

	var vectorNodes, graphNodes []RankedNode
	timedOut := false
	collected := 0

	for collected < expected && !timedOut {
		select {
		case res := <-vectorCh:
			collected++
			if res.err == nil {
				vectorNodes = res.nodes
			}
		case res := <-graphCh:
			collected++
			if res.err == nil {
				graphNodes = res.nodes
			}
		case <-timer.C:
			timedOut = true
			slog.Warn("retrieval timeout — using partial results")
		}
	}

	// Non-blocking drain after timeout
	if timedOut {
		select {
		case res := <-vectorCh:
			if res.err == nil {
				vectorNodes = res.nodes
			}
		default:
		}
		select {
		case res := <-graphCh:
			if res.err == nil {
				graphNodes = res.nodes
			}
		default:
		}
	}

	// Build lists for RRF
	var lists [][]RankedNode
	if len(vectorNodes) > 0 {
		lists = append(lists, vectorNodes)
	}
	if len(graphNodes) > 0 {
		lists = append(lists, graphNodes)
	}

	result := RRF(lists, rrfK, maxSeeds)

	slog.Info("retrieve complete",
		"retrieve_ms", time.Since(start).Milliseconds(),
		"timeout", timedOut,
		"vector_count", len(vectorNodes),
		"graph_count", len(graphNodes),
		"result_count", len(result),
	)

	return result, nil
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/dual-retriever && go test ./internal/retriever/ -v -run TestRetriever`

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/dual-retriever/internal/retriever/retriever.go services/dual-retriever/internal/retriever/retriever_test.go
git commit -m "feat(dual-retriever): add retriever orchestrator with parallel fan-out and timeout"
```

---

## Task 6: gRPC server

**Files:**
- Create: `services/dual-retriever/internal/server/server.go`
- Create: `services/dual-retriever/internal/server/server_test.go`

- [ ] **Step 1: Write the server test**

Create `services/dual-retriever/internal/server/server_test.go`:

```go
package server

import (
	"context"
	"testing"

	querypb "github.com/tersecontext/tc/services/dual-retriever/gen"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type mockRetriever struct {
	nodes []*querypb.SeedNode
	err   error
}

func (m *mockRetriever) Retrieve(ctx context.Context, embedQuery string, keywords, symbols []string, repo string, maxSeeds int) ([]*querypb.SeedNode, error) {
	return m.nodes, m.err
}

func TestServer_Retrieve(t *testing.T) {
	srv := NewQueryServer(&mockRetriever{
		nodes: []*querypb.SeedNode{
			{StableId: "a", Name: "funcA", Type: "function", Score: 0.5, RetrievalMethod: "both"},
		},
	})

	resp, err := srv.Retrieve(context.Background(), &querypb.RetrieveRequest{
		Intent: &querypb.QueryIntentResponse{
			EmbedQuery: "test",
			Keywords:   []string{"test"},
		},
		Repo:     "test-repo",
		MaxSeeds: 8,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(resp.Nodes) != 1 {
		t.Fatalf("expected 1 node, got %d", len(resp.Nodes))
	}
	if resp.Nodes[0].StableId != "a" {
		t.Errorf("expected stable_id='a', got %q", resp.Nodes[0].StableId)
	}
}

func TestServer_Retrieve_DefaultMaxSeeds(t *testing.T) {
	capture := &captureRetriever{}
	srv := NewQueryServer(capture)

	_, _ = srv.Retrieve(context.Background(), &querypb.RetrieveRequest{
		Intent: &querypb.QueryIntentResponse{EmbedQuery: "test"},
		Repo:   "repo",
		// MaxSeeds = 0 (should default to 8)
	})
	if !capture.called {
		t.Fatal("retriever was not called")
	}
	if capture.maxSeeds != 8 {
		t.Errorf("expected default maxSeeds=8, got %d", capture.maxSeeds)
	}
}

type captureRetriever struct {
	maxSeeds int
	called   bool
}

func (c *captureRetriever) Retrieve(ctx context.Context, embedQuery string, keywords, symbols []string, repo string, maxSeeds int) ([]*querypb.SeedNode, error) {
	c.called = true
	c.maxSeeds = maxSeeds
	return nil, nil
}

func TestServer_Understand_Unimplemented(t *testing.T) {
	srv := NewQueryServer(&mockRetriever{})
	_, err := srv.Understand(context.Background(), &querypb.UnderstandRequest{})
	if status.Code(err) != codes.Unimplemented {
		t.Errorf("expected Unimplemented, got %v", err)
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/dual-retriever && go test ./internal/server/ -v`

Expected: compilation error — `NewQueryServer` not defined.

- [ ] **Step 3: Implement the gRPC server**

Create `services/dual-retriever/internal/server/server.go`:

```go
package server

import (
	"context"

	querypb "github.com/tersecontext/tc/services/dual-retriever/gen"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// RetrieverService is the interface the server depends on.
type RetrieverService interface {
	Retrieve(ctx context.Context, embedQuery string, keywords, symbols []string, repo string, maxSeeds int) ([]*querypb.SeedNode, error)
}

// QueryServer implements the gRPC QueryService.
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/dual-retriever && go test ./internal/server/ -v`

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/dual-retriever/internal/server/server.go services/dual-retriever/internal/server/server_test.go
git commit -m "feat(dual-retriever): add gRPC server with Retrieve handler"
```

---

## Task 7: Entry point — main.go with gRPC + HTTP servers

**Files:**
- Create: `services/dual-retriever/cmd/main.go`

- [ ] **Step 1: Implement main.go**

Create `services/dual-retriever/cmd/main.go`:

```go
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"sync/atomic"
	"syscall"

	"github.com/neo4j/neo4j-go-driver/v5/neo4j"
	"github.com/tersecontext/tc/services/dual-retriever/internal/retriever"
	"github.com/tersecontext/tc/services/dual-retriever/internal/server"
	querypb "github.com/tersecontext/tc/services/dual-retriever/gen"
	"google.golang.org/grpc"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
)

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func main() {
	port := env("PORT", "8087")
	neo4jURL := env("NEO4J_URL", "bolt://neo4j:7687")
	neo4jUser := env("NEO4J_USER", "neo4j")
	neo4jPassword := env("NEO4J_PASSWORD", "")
	qdrantURL := env("QDRANT_URL", "http://qdrant:6333")
	embedderURL := env("EMBEDDER_URL", "http://embedder:8080")

	ctx := context.Background()
	var ready atomic.Bool

	// --- Neo4j ---
	neo4jDriver, err := neo4j.NewDriverWithContext(neo4jURL, neo4j.BasicAuth(neo4jUser, neo4jPassword, ""))
	if err != nil {
		slog.Error("failed to create neo4j driver", "error", err)
		os.Exit(1)
	}
	defer neo4jDriver.Close(ctx)

	// --- Build retriever ---
	embedder := retriever.NewHTTPEmbedder(embedderURL, nil)
	vectorSearcher := retriever.NewQdrantSearcher(qdrantURL, "nodes", nil)
	graphSearcher := retriever.NewNeo4jSearcher(neo4jDriver)
	ret := retriever.NewRetriever(embedder, vectorSearcher, graphSearcher)

	// --- gRPC server ---
	lis, err := net.Listen("tcp", ":"+port)
	if err != nil {
		slog.Error("failed to listen", "port", port, "error", err)
		os.Exit(1)
	}

	grpcServer := grpc.NewServer()
	querypb.RegisterQueryServiceServer(grpcServer, server.NewQueryServer(ret))

	healthServer := health.NewServer()
	healthpb.RegisterHealthServer(grpcServer, healthServer)

	ready.Store(true)
	healthServer.SetServingStatus("", healthpb.HealthCheckResponse_SERVING)

	// --- HTTP health server ---
	httpPort, _ := strconv.Atoi(port)
	httpAddr := fmt.Sprintf(":%d", httpPort+1)

	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	})
	mux.HandleFunc("GET /ready", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if ready.Load() {
			json.NewEncoder(w).Encode(map[string]string{"status": "ready"})
		} else {
			w.WriteHeader(http.StatusServiceUnavailable)
			json.NewEncoder(w).Encode(map[string]string{"status": "not ready"})
		}
	})
	mux.HandleFunc("GET /metrics", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})

	httpServer := &http.Server{Addr: httpAddr, Handler: mux}

	go func() {
		slog.Info("HTTP health server starting", "addr", httpAddr)
		if err := httpServer.ListenAndServe(); err != http.ErrServerClosed {
			slog.Error("HTTP server error", "error", err)
		}
	}()

	// --- Graceful shutdown ---
	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
		sig := <-sigCh
		slog.Info("shutting down", "signal", sig)
		healthServer.SetServingStatus("", healthpb.HealthCheckResponse_NOT_SERVING)
		grpcServer.GracefulStop()
		httpServer.Shutdown(ctx)
	}()

	slog.Info("gRPC server starting", "port", port)
	if err := grpcServer.Serve(lis); err != nil {
		slog.Error("gRPC server error", "error", err)
		os.Exit(1)
	}
}
```

- [ ] **Step 2: Verify it compiles**

Run: `cd services/dual-retriever && go build ./cmd/...`

Expected: binary compiles. May need `go mod tidy` first.

- [ ] **Step 3: Commit**

```bash
git add services/dual-retriever/cmd/main.go services/dual-retriever/go.mod services/dual-retriever/go.sum
git commit -m "feat(dual-retriever): add main entry point with gRPC and HTTP servers"
```

---

## Task 8: Dockerfile and docker-compose

**Files:**
- Create: `services/dual-retriever/Dockerfile`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Create Dockerfile**

Create `services/dual-retriever/Dockerfile`:

```dockerfile
FROM golang:1.23-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o dual-retriever ./cmd/main.go

FROM alpine:3.19
RUN apk add --no-cache ca-certificates
COPY --from=builder /app/dual-retriever /usr/local/bin/dual-retriever
EXPOSE 8087 8088
CMD ["dual-retriever"]
```

- [ ] **Step 2: Add dual-retriever to docker-compose.yml**

In `docker-compose.yml`, add after the `graph-writer` service block (before `neo4j-init`):

```yaml
  dual-retriever:
    build: ./services/dual-retriever
    ports:
      - "8087:8087"
      - "8088:8088"
    environment:
      NEO4J_URL: bolt://neo4j:7687
      NEO4J_USER: neo4j
      NEO4J_PASSWORD: localpassword
      QDRANT_URL: http://qdrant:6333
      EMBEDDER_URL: http://embedder:8080
    depends_on:
      neo4j:
        condition: service_healthy
      qdrant:
        condition: service_healthy
      embedder:
        condition: service_healthy
    networks:
      - tersecontext
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:8088/health"]
      interval: 10s
      timeout: 5s
      retries: 5
```

- [ ] **Step 3: Verify Docker build**

Run: `cd services/dual-retriever && docker build -t tersecontext-dual-retriever .`

Expected: image builds successfully.

- [ ] **Step 4: Commit**

```bash
git add services/dual-retriever/Dockerfile docker-compose.yml
git commit -m "feat(dual-retriever): add Dockerfile and wire into docker-compose"
```

---

## Task 9: Run all tests and verify

**Files:** none (verification only)

- [ ] **Step 1: Run all unit tests**

Run: `cd services/dual-retriever && go test ./... -v`

Expected: all tests pass across all packages.

- [ ] **Step 2: Run go vet**

Run: `cd services/dual-retriever && go vet ./...`

Expected: no issues.

- [ ] **Step 3: Final commit if any fixes needed**

If any test or vet issues were found and fixed, commit the fixes.
