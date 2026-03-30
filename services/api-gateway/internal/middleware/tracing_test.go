package middleware_test

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/tersecontext/tc/services/api-gateway/internal/middleware"
)

func TestTraceID_SetsHeader(t *testing.T) {
	handler := middleware.TraceID(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	rr := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/health", nil)
	handler.ServeHTTP(rr, req)

	if got := rr.Header().Get("X-Trace-Id"); got == "" {
		t.Fatal("X-Trace-Id header should be set")
	}
}

func TestTraceID_InjectsContext(t *testing.T) {
	var captured string
	handler := middleware.TraceID(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		captured = middleware.TraceIDFromContext(r.Context())
		w.WriteHeader(http.StatusOK)
	}))

	rr := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/health", nil)
	handler.ServeHTTP(rr, req)

	if captured == "" {
		t.Fatal("traceID should be in context")
	}
	if captured != rr.Header().Get("X-Trace-Id") {
		t.Fatal("context traceID should match header")
	}
}

func TestTraceID_UniquePerRequest(t *testing.T) {
	ids := map[string]bool{}
	handler := middleware.TraceID(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	for i := 0; i < 5; i++ {
		rr := httptest.NewRecorder()
		handler.ServeHTTP(rr, httptest.NewRequest("GET", "/", nil))
		id := rr.Header().Get("X-Trace-Id")
		if ids[id] {
			t.Fatalf("duplicate trace ID generated: %s", id)
		}
		ids[id] = true
	}
}
