// services/api-gateway/internal/handlers/query_test.go
package handlers_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	querypb "github.com/tersecontext/tc/services/api-gateway/gen"
	"github.com/tersecontext/tc/services/api-gateway/internal/handlers"
	"github.com/tersecontext/tc/services/api-gateway/internal/middleware"
	"github.com/tersecontext/tc/services/api-gateway/internal/ratelimit"
)

// --- mock implementations ---

type mockUnderstander struct{ err error }

func (m *mockUnderstander) Understand(_ context.Context, _, _, _ string) (*querypb.QueryIntentResponse, error) {
	if m.err != nil {
		return nil, m.err
	}
	return &querypb.QueryIntentResponse{RawQuery: "q", QueryType: "lookup"}, nil
}

type mockRetriever struct{ err error }

func (m *mockRetriever) Retrieve(_ context.Context, _ *querypb.QueryIntentResponse, _ string, _ int32, _ string) (*querypb.SeedNodesResponse, error) {
	if m.err != nil {
		return nil, m.err
	}
	return &querypb.SeedNodesResponse{}, nil
}

type mockExpander struct{ err error }

func (m *mockExpander) Expand(_ context.Context, _ *querypb.SeedNodesResponse, _ *querypb.QueryIntentResponse, _ int32, _ string) (*querypb.RankedSubgraphResponse, error) {
	if m.err != nil {
		return nil, m.err
	}
	return &querypb.RankedSubgraphResponse{}, nil
}

type mockSerializer struct {
	err      error
	document string
}

func (m *mockSerializer) Serialize(_ context.Context, _ *querypb.RankedSubgraphResponse, _ *querypb.QueryIntentResponse, _ string, _ string) (*querypb.ContextDocResponse, error) {
	if m.err != nil {
		return nil, m.err
	}
	return &querypb.ContextDocResponse{Document: m.document}, nil
}

func newTestQueryHandler(u handlers.Understander, ret handlers.Retriever, exp handlers.Expander, ser handlers.Serializer) *handlers.QueryHandler {
	return handlers.NewQueryHandler(u, ret, exp, ser, ratelimit.NewLimiter())
}

// --- tests ---

func TestQuery_Success(t *testing.T) {
	h := newTestQueryHandler(
		&mockUnderstander{},
		&mockRetriever{},
		&mockExpander{},
		&mockSerializer{document: "hello context"},
	)

	body := `{"repo":"acme","question":"how does auth work"}`
	rr := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/query", bytes.NewBufferString(body))
	req = req.WithContext(context.WithValue(req.Context(), middleware.TraceIDExportedKey, "test-trace"))
	req.Header.Set("Content-Type", "application/json")

	h.Handle(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
	if ct := rr.Header().Get("Content-Type"); !strings.HasPrefix(ct, "text/plain") {
		t.Fatalf("unexpected Content-Type: %s", ct)
	}
	if rr.Body.String() != "hello context" {
		t.Fatalf("unexpected body: %s", rr.Body.String())
	}
}

func TestQuery_MissingRepo(t *testing.T) {
	h := newTestQueryHandler(&mockUnderstander{}, &mockRetriever{}, &mockExpander{}, &mockSerializer{})

	body := `{"question":"what is auth"}`
	rr := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/query", bytes.NewBufferString(body))
	h.Handle(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", rr.Code)
	}
}

func TestQuery_MissingQuestion(t *testing.T) {
	h := newTestQueryHandler(&mockUnderstander{}, &mockRetriever{}, &mockExpander{}, &mockSerializer{})

	body := `{"repo":"acme"}`
	rr := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/query", bytes.NewBufferString(body))
	h.Handle(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", rr.Code)
	}
}

func TestQuery_UnderstanderFailure_NoPartialOutput(t *testing.T) {
	h := newTestQueryHandler(
		&mockUnderstander{err: errors.New("boom")},
		&mockRetriever{},
		&mockExpander{},
		&mockSerializer{document: "should not appear"},
	)

	body := `{"repo":"acme","question":"auth"}`
	rr := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/query", bytes.NewBufferString(body))
	h.Handle(rr, req)

	if rr.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500, got %d", rr.Code)
	}
	if strings.Contains(rr.Body.String(), "should not appear") {
		t.Fatal("partial document must not appear on error")
	}
	var errBody map[string]string
	json.NewDecoder(rr.Body).Decode(&errBody)
	if errBody["error"] == "" {
		t.Fatal("error body should be present")
	}
}

func TestQuery_RetrieverFailure(t *testing.T) {
	h := newTestQueryHandler(
		&mockUnderstander{},
		&mockRetriever{err: errors.New("retriever down")},
		&mockExpander{},
		&mockSerializer{},
	)

	body := `{"repo":"acme","question":"auth"}`
	rr := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/query", bytes.NewBufferString(body))
	h.Handle(rr, req)

	if rr.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500, got %d", rr.Code)
	}
}

func TestQuery_RateLimit(t *testing.T) {
	h := newTestQueryHandler(&mockUnderstander{}, &mockRetriever{}, &mockExpander{}, &mockSerializer{document: "doc"})

	body := `{"repo":"acme","question":"auth"}`
	var lastCode int
	for i := 0; i < 12; i++ {
		rr := httptest.NewRecorder()
		req := httptest.NewRequest("POST", "/query", bytes.NewBufferString(body))
		req.RemoteAddr = "1.2.3.4:9999"
		h.Handle(rr, req)
		lastCode = rr.Code
	}
	if lastCode != http.StatusTooManyRequests {
		t.Fatalf("expected 429 after exhausting limit, got %d", lastCode)
	}
}
