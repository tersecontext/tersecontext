package retriever

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// zoektSearchResponse mirrors the fields we read from zoekt's /search response.
type zoektSearchResponse struct {
	Result struct {
		Files []struct {
			FileName    string `json:"FileName"`
			Repository  string `json:"Repository"`
			LineMatches []struct {
				LineNumber int     `json:"LineNumber"`
				Score      float64 `json:"Score"`
			} `json:"LineMatches"`
		} `json:"Files"`
	} `json:"Result"`
}

func TestZoektSearcher_Search_ReturnsMappedNodes(t *testing.T) {
	// Fake zoekt HTTP server
	zoektSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		resp := zoektSearchResponse{}
		resp.Result.Files = []struct {
			FileName    string `json:"FileName"`
			Repository  string `json:"Repository"`
			LineMatches []struct {
				LineNumber int     `json:"LineNumber"`
				Score      float64 `json:"Score"`
			} `json:"LineMatches"`
		}{
			{
				FileName:   "auth/service.py",
				Repository: "test-repo",
				LineMatches: []struct {
					LineNumber int     `json:"LineNumber"`
					Score      float64 `json:"Score"`
				}{{LineNumber: 10, Score: 1.5}},
			},
		}
		json.NewEncoder(w).Encode(resp)
	}))
	defer zoektSrv.Close()

	// ZoektSearcher with a mock Neo4j resolver that returns a fixed node
	searcher := &ZoektSearcher{
		zoektURL:   zoektSrv.URL,
		httpClient: &http.Client{},
		resolver:   &fakeNodeResolver{nodes: []RankedNode{{StableID: "sha:fn_auth", Name: "authenticate", Type: "function", Source: "graph"}}},
	}

	nodes, err := searcher.Search(context.Background(), []string{"auth"}, []string{"authenticate"}, "test-repo", 10)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(nodes) == 0 {
		t.Fatal("expected at least one node, got 0")
	}
	if nodes[0].StableID != "sha:fn_auth" {
		t.Errorf("got StableID %q, want %q", nodes[0].StableID, "sha:fn_auth")
	}
}

func TestZoektSearcher_Search_EmptyKeywordsAndSymbols_ReturnsEmpty(t *testing.T) {
	searcher := &ZoektSearcher{
		zoektURL:   "http://unused",
		httpClient: &http.Client{},
		resolver:   &fakeNodeResolver{},
	}
	nodes, err := searcher.Search(context.Background(), nil, nil, "repo", 10)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(nodes) != 0 {
		t.Errorf("expected 0 nodes for empty query, got %d", len(nodes))
	}
}

// fakeNodeResolver implements nodeResolver for tests.
type fakeNodeResolver struct {
	nodes []RankedNode
}

func (f *fakeNodeResolver) Resolve(ctx context.Context, matches []fileLineMatch, repo string) ([]RankedNode, error) {
	return f.nodes, nil
}

func TestBuildZoektQuery(t *testing.T) {
	q := buildZoektQuery([]string{"auth", "login"}, []string{"AuthService"})
	if !strings.Contains(q, "sym:AuthService") {
		t.Errorf("expected sym: prefix for symbol, got %q", q)
	}
	if !strings.Contains(q, "auth") {
		t.Errorf("expected keyword 'auth' in query, got %q", q)
	}
	if !strings.Contains(q, " OR ") {
		t.Errorf("expected OR separator in query, got %q", q)
	}
}

func TestBuildZoektQuery_EmptyInputs(t *testing.T) {
	q := buildZoektQuery(nil, nil)
	if q != "" {
		t.Errorf("expected empty query for nil inputs, got %q", q)
	}
}

func TestZoektSearcher_Search_NonOKStatus_ReturnsError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte("internal error"))
	}))
	defer srv.Close()

	searcher := &ZoektSearcher{
		zoektURL:   srv.URL,
		httpClient: &http.Client{},
		resolver:   &fakeNodeResolver{},
	}
	_, err := searcher.Search(context.Background(), []string{"auth"}, nil, "repo", 10)
	if err == nil {
		t.Fatal("expected error for non-200 status, got nil")
	}
}

// fakeErrorResolver is a nodeResolver that always returns an error.
type fakeErrorResolver struct{}

func (f *fakeErrorResolver) Resolve(ctx context.Context, matches []fileLineMatch, repo string) ([]RankedNode, error) {
	return nil, fmt.Errorf("resolver failure")
}

func TestZoektSearcher_Search_ResolverError_Propagated(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		resp := zoektSearchResponse{}
		resp.Result.Files = []struct {
			FileName    string `json:"FileName"`
			Repository  string `json:"Repository"`
			LineMatches []struct {
				LineNumber int     `json:"LineNumber"`
				Score      float64 `json:"Score"`
			} `json:"LineMatches"`
		}{
			{FileName: "main.go", Repository: "repo", LineMatches: []struct {
				LineNumber int     `json:"LineNumber"`
				Score      float64 `json:"Score"`
			}{{LineNumber: 1, Score: 1.0}}},
		}
		json.NewEncoder(w).Encode(resp)
	}))
	defer srv.Close()

	searcher := &ZoektSearcher{
		zoektURL:   srv.URL,
		httpClient: &http.Client{},
		resolver:   &fakeErrorResolver{},
	}
	_, err := searcher.Search(context.Background(), []string{"auth"}, nil, "repo", 10)
	if err == nil {
		t.Fatal("expected error from resolver, got nil")
	}
}
