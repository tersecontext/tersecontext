# API Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the api-gateway service — the single external HTTP entry point that orchestrates query-understander → dual-retriever → subgraph-expander → serializer and streams the result back to the caller.

**Architecture:** Pure Go HTTP server on port 8090. Calls query-understander over HTTP/JSON and the three other services over gRPC. Token-bucket rate limiting per repo+IP in-memory. Redis XADD for async index triggers.

**Tech Stack:** Go 1.24, net/http, google.golang.org/grpc v1.79.3, github.com/redis/go-redis/v9, github.com/google/uuid, log/slog (stdlib)

---

## File Map

| File | Responsibility |
|------|---------------|
| `services/api-gateway/go.mod` | Module definition; update from stub go 1.23 |
| `services/api-gateway/gen/query.pb.go` | Copied verbatim from serializer/gen |
| `services/api-gateway/gen/query_grpc.pb.go` | Copied verbatim from serializer/gen |
| `services/api-gateway/internal/ratelimit/bucket.go` | Token bucket per repo+IP key |
| `services/api-gateway/internal/ratelimit/bucket_test.go` | Unit tests: fill, exhaust, refill |
| `services/api-gateway/internal/middleware/tracing.go` | UUID inject into context + X-Trace-Id header |
| `services/api-gateway/internal/middleware/tracing_test.go` | Verify header set, context value |
| `services/api-gateway/internal/middleware/logging.go` | slog request logging |
| `services/api-gateway/internal/handlers/health.go` | /health /ready /metrics |
| `services/api-gateway/internal/handlers/index.go` | POST /index → Redis XADD |
| `services/api-gateway/internal/handlers/index_test.go` | Stub StreamAdder, verify XADD args |
| `services/api-gateway/internal/handlers/query.go` | POST /query orchestration |
| `services/api-gateway/internal/handlers/query_test.go` | Mock clients, verify order and error paths |
| `services/api-gateway/internal/clients/understander.go` | HTTP client → query-understander |
| `services/api-gateway/internal/clients/retriever.go` | gRPC client → dual-retriever |
| `services/api-gateway/internal/clients/expander.go` | gRPC client → subgraph-expander |
| `services/api-gateway/internal/clients/serializer.go` | gRPC client → serializer |
| `services/api-gateway/cmd/main.go` | Wire everything; HTTP server; graceful shutdown |
| `services/api-gateway/Dockerfile` | Multi-stage build |
| `docker-compose.yml` | Add api-gateway + query-understander; fix serializer port |

---

## Task 1: Scaffold module and copy gen files

**Files:**
- Modify: `services/api-gateway/go.mod`
- Create: `services/api-gateway/gen/query.pb.go`
- Create: `services/api-gateway/gen/query_grpc.pb.go`

- [ ] **Step 1: Update go.mod**

Replace the existing stub content:

```
module github.com/tersecontext/tc/services/api-gateway

go 1.24.0

toolchain go1.24.1

require (
	github.com/google/uuid v1.6.0
	github.com/redis/go-redis/v9 v9.11.0
	google.golang.org/grpc v1.79.3
	google.golang.org/protobuf v1.36.11
)
```

- [ ] **Step 2: Copy gen files verbatim**

```bash
cp services/serializer/gen/query.pb.go services/api-gateway/gen/query.pb.go
cp services/serializer/gen/query_grpc.pb.go services/api-gateway/gen/query_grpc.pb.go
```

The files compile as-is under the api-gateway module. No edits needed — same pattern used by dual-retriever and subgraph-expander.

- [ ] **Step 3: Run go mod tidy to populate go.sum**

```bash
cd services/api-gateway && go mod tidy
```

Expected: `go.sum` created, no errors.

- [ ] **Step 4: Verify the gen package compiles**

```bash
cd services/api-gateway && go build ./gen/...
```

Expected: exits 0, no output.

- [ ] **Step 5: Commit**

```bash
git add services/api-gateway/go.mod services/api-gateway/go.sum services/api-gateway/gen/
git commit -m "feat(api-gateway): scaffold module and copy proto gen files"
```

---

## Task 2: Rate limiter (TDD)

**Files:**
- Create: `services/api-gateway/internal/ratelimit/bucket.go`
- Create: `services/api-gateway/internal/ratelimit/bucket_test.go`

- [ ] **Step 1: Write the failing tests**

```go
// services/api-gateway/internal/ratelimit/bucket_test.go
package ratelimit_test

import (
	"testing"
	"time"

	"github.com/tersecontext/tc/services/api-gateway/internal/ratelimit"
)

func TestAllow_UnderLimit(t *testing.T) {
	l := ratelimit.NewLimiter()
	for i := 0; i < 10; i++ {
		if !l.Allow("repo:ip") {
			t.Fatalf("request %d should be allowed", i+1)
		}
	}
}

func TestAllow_ExceedsLimit(t *testing.T) {
	l := ratelimit.NewLimiter()
	for i := 0; i < 10; i++ {
		l.Allow("repo:ip")
	}
	if l.Allow("repo:ip") {
		t.Fatal("11th request should be denied")
	}
}

func TestAllow_DifferentKeysIndependent(t *testing.T) {
	l := ratelimit.NewLimiter()
	for i := 0; i < 10; i++ {
		l.Allow("repo-a:ip")
	}
	// repo-b bucket is full — should still allow
	if !l.Allow("repo-b:ip") {
		t.Fatal("different key should have its own bucket")
	}
}

func TestAllow_RefillOverTime(t *testing.T) {
	l := ratelimit.NewLimiterWithClock(func() time.Time {
		return time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	})
	for i := 0; i < 10; i++ {
		l.Allow("repo:ip")
	}
	// Advance clock by 6 seconds → 1 token refilled (10/60 * 6 = 1.0)
	l.SetClock(func() time.Time {
		return time.Date(2026, 1, 1, 0, 0, 6, 0, time.UTC)
	})
	if !l.Allow("repo:ip") {
		t.Fatal("should allow after refill time")
	}
	if l.Allow("repo:ip") {
		t.Fatal("only 1 token should have refilled")
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/api-gateway && go test ./internal/ratelimit/... -v
```

Expected: compile error — package not found.

- [ ] **Step 3: Implement bucket.go**

```go
// services/api-gateway/internal/ratelimit/bucket.go
package ratelimit

import (
	"sync"
	"time"
)

const (
	maxTokens  = 10
	refillRate = 10.0 / 60.0 // tokens per second
)

type clockFn func() time.Time

type bucket struct {
	mu         sync.Mutex
	tokens     float64
	lastRefill time.Time
}

// Limiter is a per-key token bucket rate limiter.
type Limiter struct {
	buckets sync.Map
	clock   clockFn
}

// NewLimiter creates a Limiter using the real clock.
func NewLimiter() *Limiter {
	return &Limiter{clock: time.Now}
}

// NewLimiterWithClock creates a Limiter with an injectable clock for testing.
func NewLimiterWithClock(clock clockFn) *Limiter {
	return &Limiter{clock: clock}
}

// SetClock replaces the clock function (test helper).
func (l *Limiter) SetClock(clock clockFn) {
	l.clock = clock
}

// Allow returns true if the key is within the rate limit, false otherwise.
func (l *Limiter) Allow(key string) bool {
	now := l.clock()
	v, _ := l.buckets.LoadOrStore(key, &bucket{tokens: maxTokens, lastRefill: now})
	b := v.(*bucket)

	b.mu.Lock()
	defer b.mu.Unlock()

	elapsed := now.Sub(b.lastRefill).Seconds()
	b.tokens += elapsed * refillRate
	if b.tokens > maxTokens {
		b.tokens = maxTokens
	}
	b.lastRefill = now

	if b.tokens < 1 {
		return false
	}
	b.tokens--
	return true
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd services/api-gateway && go test ./internal/ratelimit/... -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/api-gateway/internal/ratelimit/
git commit -m "feat(api-gateway): token bucket rate limiter with injectable clock"
```

---

## Task 3: Tracing middleware (TDD)

**Files:**
- Create: `services/api-gateway/internal/middleware/tracing.go`
- Create: `services/api-gateway/internal/middleware/tracing_test.go`
- Create: `services/api-gateway/internal/middleware/logging.go`

- [ ] **Step 1: Write failing tests for tracing**

```go
// services/api-gateway/internal/middleware/tracing_test.go
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
```

- [ ] **Step 2: Run to verify compile failure**

```bash
cd services/api-gateway && go test ./internal/middleware/... -v
```

Expected: compile error.

- [ ] **Step 3: Implement tracing.go**

```go
// services/api-gateway/internal/middleware/tracing.go
package middleware

import (
	"context"
	"net/http"

	"github.com/google/uuid"
)

type contextKey string

const traceIDKey contextKey = "traceID"

// TraceID is an HTTP middleware that generates a UUID trace ID per request,
// sets the X-Trace-Id response header, and injects the ID into the context.
func TraceID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := uuid.New().String()
		w.Header().Set("X-Trace-Id", id)
		ctx := context.WithValue(r.Context(), traceIDKey, id)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// TraceIDFromContext retrieves the trace ID from a context, or "" if absent.
func TraceIDFromContext(ctx context.Context) string {
	if id, ok := ctx.Value(traceIDKey).(string); ok {
		return id
	}
	return ""
}
```

- [ ] **Step 4: Implement logging.go** (no tests — thin wrapper over slog)

```go
// services/api-gateway/internal/middleware/logging.go
package middleware

import (
	"log/slog"
	"net/http"
	"time"
)

// Logging logs method, path, duration, and trace ID for every request.
func Logging(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		next.ServeHTTP(w, r)
		slog.Info("request",
			"method", r.Method,
			"path", r.URL.Path,
			"duration_ms", time.Since(start).Milliseconds(),
			"trace_id", TraceIDFromContext(r.Context()),
		)
	})
}
```

- [ ] **Step 5: Run tests**

```bash
cd services/api-gateway && go test ./internal/middleware/... -v
```

Expected: all 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add services/api-gateway/internal/middleware/
git commit -m "feat(api-gateway): tracing and logging middleware"
```

---

## Task 4: Health handler

**Files:**
- Create: `services/api-gateway/internal/handlers/health.go`

No unit tests — these are trivial JSON wrappers; covered by the CLAUDE.md curl verification.

- [ ] **Step 1: Implement health.go**

```go
// services/api-gateway/internal/handlers/health.go
package handlers

import (
	"encoding/json"
	"net/http"
	"sync/atomic"
)

// Health handles GET /health.
func Health(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"status":  "ok",
		"service": "api-gateway",
		"version": "0.1.0",
	})
}

// Ready handles GET /ready. Uses the ready flag set after startup.
func Ready(ready *atomic.Bool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if ready.Load() {
			json.NewEncoder(w).Encode(map[string]string{"status": "ready"})
		} else {
			w.WriteHeader(http.StatusServiceUnavailable)
			json.NewEncoder(w).Encode(map[string]string{"status": "not ready"})
		}
	}
}

// Metrics handles GET /metrics (stub — consistent with other services).
func Metrics(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
}
```

- [ ] **Step 2: Verify it compiles**

```bash
cd services/api-gateway && go build ./internal/handlers/...
```

Expected: exits 0.

- [ ] **Step 3: Commit**

```bash
git add services/api-gateway/internal/handlers/health.go
git commit -m "feat(api-gateway): health/ready/metrics handlers"
```

---

## Task 5: Index handler (TDD)

**Files:**
- Create: `services/api-gateway/internal/handlers/index.go`
- Create: `services/api-gateway/internal/handlers/index_test.go`

- [ ] **Step 1: Write failing tests**

```go
// services/api-gateway/internal/handlers/index_test.go
package handlers_test

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/redis/go-redis/v9"
	"github.com/tersecontext/tc/services/api-gateway/internal/handlers"
)

// stubStream captures XAdd calls for inspection.
type stubStream struct {
	lastArgs *redis.XAddArgs
	err      error
}

func (s *stubStream) XAdd(_ context.Context, args *redis.XAddArgs) *redis.StringCmd {
	s.lastArgs = args
	cmd := redis.NewStringCmd(context.Background())
	if s.err != nil {
		cmd.SetErr(s.err)
	}
	return cmd
}

func TestIndex_Success_FullRescan(t *testing.T) {
	stub := &stubStream{}
	h := handlers.NewIndexHandler(stub)

	body := `{"repo_path":"/repos/acme","full_rescan":true}`
	rr := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/index", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	h.Handle(rr, req)

	if rr.Code != http.StatusAccepted {
		t.Fatalf("expected 202, got %d", rr.Code)
	}
	var resp map[string]string
	json.NewDecoder(rr.Body).Decode(&resp)
	if resp["job_id"] == "" {
		t.Fatal("job_id should be non-empty")
	}
	if resp["message"] != "indexing started" {
		t.Fatalf("unexpected message: %s", resp["message"])
	}
	if stub.lastArgs == nil {
		t.Fatal("XAdd should have been called")
	}
	if stub.lastArgs.Stream != "stream:file-changed" {
		t.Fatalf("wrong stream: %s", stub.lastArgs.Stream)
	}
	vals := stub.lastArgs.Values.(map[string]any)
	if vals["diff_type"] != "full_rescan" {
		t.Fatalf("expected diff_type full_rescan, got %v", vals["diff_type"])
	}
	if vals["repo_path"] != "/repos/acme" {
		t.Fatalf("wrong repo_path: %v", vals["repo_path"])
	}
	if vals["job_id"] != resp["job_id"] {
		t.Fatal("job_id in stream must match response")
	}
}

func TestIndex_Success_Incremental(t *testing.T) {
	stub := &stubStream{}
	h := handlers.NewIndexHandler(stub)

	body := `{"repo_path":"/repos/acme","full_rescan":false}`
	rr := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/index", bytes.NewBufferString(body))
	h.Handle(rr, req)

	if rr.Code != http.StatusAccepted {
		t.Fatalf("expected 202, got %d", rr.Code)
	}
	vals := stub.lastArgs.Values.(map[string]any)
	if vals["diff_type"] != "incremental" {
		t.Fatalf("expected incremental, got %v", vals["diff_type"])
	}
}

func TestIndex_MissingRepoPath(t *testing.T) {
	stub := &stubStream{}
	h := handlers.NewIndexHandler(stub)

	rr := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/index", bytes.NewBufferString(`{}`))
	h.Handle(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", rr.Code)
	}
}
```

- [ ] **Step 2: Run to verify compile failure**

```bash
cd services/api-gateway && go test ./internal/handlers/... -run TestIndex -v
```

Expected: compile error — handlers package not found.

- [ ] **Step 3: Implement index.go**

```go
// services/api-gateway/internal/handlers/index.go
package handlers

import (
	"context"
	"encoding/json"
	"net/http"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// StreamAdder is the minimal Redis interface the index handler needs.
type StreamAdder interface {
	XAdd(ctx context.Context, args *redis.XAddArgs) *redis.StringCmd
}

// IndexHandler handles POST /index.
type IndexHandler struct {
	stream StreamAdder
}

// NewIndexHandler creates an IndexHandler backed by the given stream.
func NewIndexHandler(stream StreamAdder) *IndexHandler {
	return &IndexHandler{stream: stream}
}

type indexRequest struct {
	RepoPath   string `json:"repo_path"`
	FullRescan bool   `json:"full_rescan"`
}

// Handle processes POST /index requests.
func (h *IndexHandler) Handle(w http.ResponseWriter, r *http.Request) {
	var req indexRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.RepoPath == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "repo_path is required"})
		return
	}

	jobID := uuid.New().String()
	diffType := "incremental"
	if req.FullRescan {
		diffType = "full_rescan"
	}

	if err := h.stream.XAdd(r.Context(), &redis.XAddArgs{
		Stream: "stream:file-changed",
		Values: map[string]any{
			"repo_path": req.RepoPath,
			"diff_type": diffType,
			"job_id":    jobID,
		},
	}).Err(); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": "failed to enqueue job"})
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(map[string]string{
		"job_id":  jobID,
		"message": "indexing started",
	})
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd services/api-gateway && go test ./internal/handlers/... -run TestIndex -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/api-gateway/internal/handlers/index.go services/api-gateway/internal/handlers/index_test.go
git commit -m "feat(api-gateway): index handler with Redis stream dispatch"
```

---

## Task 6: gRPC and HTTP client wrappers

**Files:**
- Create: `services/api-gateway/internal/clients/understander.go`
- Create: `services/api-gateway/internal/clients/retriever.go`
- Create: `services/api-gateway/internal/clients/expander.go`
- Create: `services/api-gateway/internal/clients/serializer.go`

No unit tests — these are thin wrappers; covered by integration. Focus is correct field mapping and traceID propagation.

- [ ] **Step 1: Implement understander.go**

```go
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
	baseURL string
	http    *http.Client
}

// NewUnderstanderClient creates a client targeting baseURL (e.g. "http://query-understander:8080").
func NewUnderstanderClient(baseURL string) *UnderstanderClient {
	return &UnderstanderClient{
		baseURL: baseURL,
		http:    &http.Client{Timeout: 30 * time.Second},
	}
}

// Understand calls POST /understand and maps the response to a proto QueryIntentResponse.
func (c *UnderstanderClient) Understand(ctx context.Context, question, repo, traceID string) (*querypb.QueryIntentResponse, error) {
	payload, _ := json.Marshal(understandRequest{Question: question, Repo: repo})
	req, err := http.NewRequestWithContext(ctx, "POST", c.baseURL+"/understand", bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Trace-Id", traceID)

	resp, err := c.http.Do(req)
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
```

- [ ] **Step 2: Implement retriever.go**

```go
// services/api-gateway/internal/clients/retriever.go
package clients

import (
	"context"

	querypb "github.com/tersecontext/tc/services/api-gateway/gen"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
)

// RetrieverClient calls the dual-retriever gRPC service.
type RetrieverClient struct {
	client querypb.QueryServiceClient
}

// NewRetrieverClient dials addr (e.g. "dual-retriever:8087").
func NewRetrieverClient(addr string) (*RetrieverClient, error) {
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, err
	}
	return &RetrieverClient{client: querypb.NewQueryServiceClient(conn)}, nil
}

// Retrieve calls QueryService.Retrieve with traceID propagated as gRPC metadata.
func (c *RetrieverClient) Retrieve(ctx context.Context, intent *querypb.QueryIntentResponse, repo string, maxSeeds int32, traceID string) (*querypb.SeedNodesResponse, error) {
	ctx = metadata.AppendToOutgoingContext(ctx, "x-trace-id", traceID)
	return c.client.Retrieve(ctx, &querypb.RetrieveRequest{
		Intent:   intent,
		Repo:     repo,
		MaxSeeds: maxSeeds,
	})
}
```

- [ ] **Step 3: Implement expander.go**

```go
// services/api-gateway/internal/clients/expander.go
package clients

import (
	"context"

	querypb "github.com/tersecontext/tc/services/api-gateway/gen"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
)

// ExpanderClient calls the subgraph-expander gRPC service.
type ExpanderClient struct {
	client querypb.QueryServiceClient
}

// NewExpanderClient dials addr (e.g. "subgraph-expander:8088").
func NewExpanderClient(addr string) (*ExpanderClient, error) {
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, err
	}
	return &ExpanderClient{client: querypb.NewQueryServiceClient(conn)}, nil
}

// Expand calls QueryService.Expand.
func (c *ExpanderClient) Expand(ctx context.Context, seeds *querypb.SeedNodesResponse, intent *querypb.QueryIntentResponse, maxTokens int32, traceID string) (*querypb.RankedSubgraphResponse, error) {
	ctx = metadata.AppendToOutgoingContext(ctx, "x-trace-id", traceID)
	return c.client.Expand(ctx, &querypb.ExpandRequest{
		Seeds:     seeds,
		Intent:    intent,
		MaxTokens: maxTokens,
	})
}
```

- [ ] **Step 4: Implement serializer.go**

```go
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
	client querypb.QueryServiceClient
}

// NewSerializerClient dials addr (e.g. "serializer:8089").
func NewSerializerClient(addr string) (*SerializerClient, error) {
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, err
	}
	return &SerializerClient{client: querypb.NewQueryServiceClient(conn)}, nil
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
```

- [ ] **Step 5: Verify all clients compile**

```bash
cd services/api-gateway && go build ./internal/clients/...
```

Expected: exits 0.

- [ ] **Step 6: Commit**

```bash
git add services/api-gateway/internal/clients/
git commit -m "feat(api-gateway): gRPC and HTTP client wrappers"
```

---

## Task 7: Query handler (TDD)

**Files:**
- Create: `services/api-gateway/internal/handlers/query.go`
- Create: `services/api-gateway/internal/handlers/query_test.go`

- [ ] **Step 1: Write failing tests**

```go
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
```

- [ ] **Step 2: Run to verify compile failure**

```bash
cd services/api-gateway && go test ./internal/handlers/... -run TestQuery -v
```

Expected: compile error.

- [ ] **Step 3: Implement query.go**

```go
// services/api-gateway/internal/handlers/query.go
package handlers

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"time"

	querypb "github.com/tersecontext/tc/services/api-gateway/gen"
	"github.com/tersecontext/tc/services/api-gateway/internal/middleware"
	"github.com/tersecontext/tc/services/api-gateway/internal/ratelimit"
)

// Understander is the interface the query handler uses to call query-understander.
type Understander interface {
	Understand(ctx context.Context, question, repo, traceID string) (*querypb.QueryIntentResponse, error)
}

// Retriever is the interface for the dual-retriever call.
type Retriever interface {
	Retrieve(ctx context.Context, intent *querypb.QueryIntentResponse, repo string, maxSeeds int32, traceID string) (*querypb.SeedNodesResponse, error)
}

// Expander is the interface for the subgraph-expander call.
type Expander interface {
	Expand(ctx context.Context, seeds *querypb.SeedNodesResponse, intent *querypb.QueryIntentResponse, maxTokens int32, traceID string) (*querypb.RankedSubgraphResponse, error)
}

// Serializer is the interface for the serializer call.
type Serializer interface {
	Serialize(ctx context.Context, subgraph *querypb.RankedSubgraphResponse, intent *querypb.QueryIntentResponse, repo string, traceID string) (*querypb.ContextDocResponse, error)
}

// QueryHandler handles POST /query.
type QueryHandler struct {
	understander Understander
	retriever    Retriever
	expander     Expander
	serializer   Serializer
	limiter      *ratelimit.Limiter
}

// NewQueryHandler creates a QueryHandler with all dependencies injected.
func NewQueryHandler(u Understander, ret Retriever, exp Expander, ser Serializer, limiter *ratelimit.Limiter) *QueryHandler {
	return &QueryHandler{u, ret, exp, ser, limiter}
}

type queryRequest struct {
	Repo     string      `json:"repo"`
	Question string      `json:"question"`
	Options  queryOptions `json:"options"`
}

type queryOptions struct {
	MaxTokens int32  `json:"max_tokens"`
	HopDepth  int    `json:"hop_depth"`
	QueryType string `json:"query_type"`
	Scope     string `json:"scope"`
}

func writeError(w http.ResponseWriter, status int, service, traceID, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(map[string]string{
		"error":    msg,
		"service":  service,
		"trace_id": traceID,
	})
}

// Handle processes POST /query requests.
func (h *QueryHandler) Handle(w http.ResponseWriter, r *http.Request) {
	traceID := middleware.TraceIDFromContext(r.Context())

	var req queryRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid request body"})
		return
	}
	if req.Repo == "" || req.Question == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "repo and question are required"})
		return
	}

	ip, _, _ := net.SplitHostPort(r.RemoteAddr)
	key := fmt.Sprintf("%s:%s", req.Repo, ip)
	if !h.limiter.Allow(key) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Retry-After", "60")
		w.WriteHeader(http.StatusTooManyRequests)
		json.NewEncoder(w).Encode(map[string]string{"error": "rate limit exceeded"})
		return
	}

	maxTokens := req.Options.MaxTokens
	if maxTokens == 0 {
		maxTokens = 2000
	}

	ctx := r.Context()

	// 1. Understand
	start := time.Now()
	intent, err := h.understander.Understand(ctx, req.Question, req.Repo, traceID)
	if err != nil {
		slog.Error("understander failed", "trace_id", traceID, "duration_ms", time.Since(start).Milliseconds(), "error", err)
		writeError(w, http.StatusInternalServerError, "understander", traceID, "upstream failure")
		return
	}
	slog.Info("understand ok", "trace_id", traceID, "duration_ms", time.Since(start).Milliseconds())

	// 2. Retrieve
	start = time.Now()
	seeds, err := h.retriever.Retrieve(ctx, intent, req.Repo, 20, traceID)
	if err != nil {
		slog.Error("retriever failed", "trace_id", traceID, "duration_ms", time.Since(start).Milliseconds(), "error", err)
		writeError(w, http.StatusInternalServerError, "dual-retriever", traceID, "upstream failure")
		return
	}
	slog.Info("retrieve ok", "trace_id", traceID, "seeds", len(seeds.GetNodes()), "duration_ms", time.Since(start).Milliseconds())

	// 3. Expand
	start = time.Now()
	subgraph, err := h.expander.Expand(ctx, seeds, intent, maxTokens, traceID)
	if err != nil {
		slog.Error("expander failed", "trace_id", traceID, "duration_ms", time.Since(start).Milliseconds(), "error", err)
		writeError(w, http.StatusInternalServerError, "subgraph-expander", traceID, "upstream failure")
		return
	}
	slog.Info("expand ok", "trace_id", traceID, "nodes", len(subgraph.GetNodes()), "duration_ms", time.Since(start).Milliseconds())

	// 4. Serialize
	start = time.Now()
	doc, err := h.serializer.Serialize(ctx, subgraph, intent, req.Repo, traceID)
	if err != nil {
		slog.Error("serializer failed", "trace_id", traceID, "duration_ms", time.Since(start).Milliseconds(), "error", err)
		writeError(w, http.StatusInternalServerError, "serializer", traceID, "upstream failure")
		return
	}
	slog.Info("serialize ok", "trace_id", traceID, "tokens", doc.GetTokenCount(), "duration_ms", time.Since(start).Milliseconds())

	// Stream response
	w.Header().Set("Content-Type", "text/plain")
	w.Header().Set("Transfer-Encoding", "chunked")
	w.WriteHeader(http.StatusOK)
	w.Write([]byte(doc.GetDocument()))
	if f, ok := w.(http.Flusher); ok {
		f.Flush()
	}
}
```

Note: The test imports `middleware.TraceIDExportedKey` — add an exported alias to tracing.go:

```go
// Add to tracing.go:
// TraceIDExportedKey is exported so test packages can inject a trace ID directly.
var TraceIDExportedKey = traceIDKey
```

- [ ] **Step 4: Add exported key alias to tracing.go**

Add at the end of `services/api-gateway/internal/middleware/tracing.go`:

```go
// TraceIDExportedKey exposes the context key so test packages can inject trace IDs.
var TraceIDExportedKey = traceIDKey
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd services/api-gateway && go test ./internal/handlers/... -v
```

Expected: all tests PASS (both index and query tests).

- [ ] **Step 6: Commit**

```bash
git add services/api-gateway/internal/handlers/query.go services/api-gateway/internal/handlers/query_test.go services/api-gateway/internal/middleware/tracing.go
git commit -m "feat(api-gateway): query handler with orchestration and rate limiting"
```

---

## Task 8: cmd/main.go and server wiring

**Files:**
- Create: `services/api-gateway/cmd/main.go`

- [ ] **Step 1: Implement cmd/main.go**

```go
// services/api-gateway/cmd/main.go
package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"sync/atomic"
	"syscall"

	"github.com/redis/go-redis/v9"
	"github.com/tersecontext/tc/services/api-gateway/internal/clients"
	"github.com/tersecontext/tc/services/api-gateway/internal/handlers"
	"github.com/tersecontext/tc/services/api-gateway/internal/middleware"
	"github.com/tersecontext/tc/services/api-gateway/internal/ratelimit"
)

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func main() {
	port := env("PORT", "8090")
	understanderURL := env("UNDERSTANDER_URL", "http://localhost:8086")
	retrieverAddr := env("RETRIEVER_ADDR", "localhost:8087")
	expanderAddr := env("EXPANDER_ADDR", "localhost:8088")
	serializerAddr := env("SERIALIZER_ADDR", "localhost:8089")
	redisURL := env("REDIS_URL", "redis://localhost:6379")

	// Redis
	opts, err := redis.ParseURL(redisURL)
	if err != nil {
		slog.Error("invalid REDIS_URL", "error", err)
		os.Exit(1)
	}
	redisClient := redis.NewClient(opts)

	// gRPC clients
	retriever, err := clients.NewRetrieverClient(retrieverAddr)
	if err != nil {
		slog.Error("failed to create retriever client", "error", err)
		os.Exit(1)
	}
	expander, err := clients.NewExpanderClient(expanderAddr)
	if err != nil {
		slog.Error("failed to create expander client", "error", err)
		os.Exit(1)
	}
	serializer, err := clients.NewSerializerClient(serializerAddr)
	if err != nil {
		slog.Error("failed to create serializer client", "error", err)
		os.Exit(1)
	}

	// HTTP client
	understander := clients.NewUnderstanderClient(understanderURL)

	// Handlers
	limiter := ratelimit.NewLimiter()
	queryHandler := handlers.NewQueryHandler(understander, retriever, expander, serializer, limiter)
	indexHandler := handlers.NewIndexHandler(redisClient)

	var ready atomic.Bool

	// Routes
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", handlers.Health)
	mux.HandleFunc("GET /ready", handlers.Ready(&ready))
	mux.HandleFunc("GET /metrics", handlers.Metrics)
	mux.HandleFunc("POST /query", queryHandler.Handle)
	mux.HandleFunc("POST /index", indexHandler.Handle)

	// Middleware chain: TraceID → Logging → mux
	handler := middleware.TraceID(middleware.Logging(mux))

	srv := &http.Server{
		Addr:    ":" + port,
		Handler: handler,
	}

	ready.Store(true)

	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
		sig := <-sigCh
		slog.Info("shutting down", "signal", sig)
		ready.Store(false)
		srv.Shutdown(context.Background())
	}()

	slog.Info("api-gateway starting", "port", port)
	if err := srv.ListenAndServe(); err != http.ErrServerClosed {
		slog.Error("server error", "error", err)
		os.Exit(1)
	}
}

// Ensure *redis.Client satisfies handlers.StreamAdder at compile time.
var _ handlers.StreamAdder = (*redis.Client)(nil)
```

Note: `*redis.Client` already implements `XAdd(ctx, *redis.XAddArgs) *redis.StringCmd`, so it satisfies `StreamAdder` directly.

- [ ] **Step 2: Build the binary**

```bash
cd services/api-gateway && go build ./cmd/...
```

Expected: exits 0, binary produced.

- [ ] **Step 3: Run all tests**

```bash
cd services/api-gateway && go test ./...
```

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add services/api-gateway/cmd/main.go
git commit -m "feat(api-gateway): main.go wiring all components"
```

---

## Task 9: Dockerfile

**Files:**
- Create: `services/api-gateway/Dockerfile`

- [ ] **Step 1: Write Dockerfile** (mirrors subgraph-expander exactly)

```dockerfile
FROM golang:1.24-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o api-gateway ./cmd/main.go

FROM alpine:3.19
RUN apk add --no-cache ca-certificates
COPY --from=builder /app/api-gateway /usr/local/bin/api-gateway
EXPOSE 8090
CMD ["api-gateway"]
```

- [ ] **Step 2: Build the image**

```bash
cd services/api-gateway && docker build -t api-gateway:dev .
```

Expected: build succeeds, image created.

- [ ] **Step 3: Commit**

```bash
git add services/api-gateway/Dockerfile
git commit -m "feat(api-gateway): Dockerfile"
```

---

## Task 10: docker-compose updates

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Remove 8090 host port from serializer**

In `docker-compose.yml`, change the serializer ports from:

```yaml
  serializer:
    ...
    ports:
      - "8089:8089"
      - "8090:8090"
```

to:

```yaml
  serializer:
    ...
    ports:
      - "8089:8089"
```

The serializer's HTTP health server still listens on container-internal port 8090 (verified by its healthcheck `http://localhost:8090/health`) — only the host mapping is removed.

- [ ] **Step 2: Add query-understander service**

Add after the `ollama` service block:

```yaml
  query-understander:
    build: ./services/query-understander
    ports:
      - "8086:8080"
    environment:
      REDIS_URL: redis://redis:6379
      LLM_BASE_URL: http://ollama:11434
    depends_on:
      redis:
        condition: service_healthy
      ollama:
        condition: service_started
    networks:
      - tersecontext
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 10s
      timeout: 5s
      retries: 5
```

- [ ] **Step 3: Add api-gateway service**

Add after the `serializer` service block:

```yaml
  api-gateway:
    build: ./services/api-gateway
    ports:
      - "8090:8090"
    environment:
      PORT: "8090"
      UNDERSTANDER_URL: http://query-understander:8080
      RETRIEVER_ADDR: dual-retriever:8087
      EXPANDER_ADDR: subgraph-expander:8088
      SERIALIZER_ADDR: serializer:8089
      REDIS_URL: redis://redis:6379
    depends_on:
      query-understander:
        condition: service_healthy
      dual-retriever:
        condition: service_healthy
      subgraph-expander:
        condition: service_healthy
      serializer:
        condition: service_healthy
    networks:
      - tersecontext
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:8090/health"]
      interval: 10s
      timeout: 5s
      retries: 5
```

Note: uses `wget` (available in alpine) consistent with other Go services' healthchecks.

- [ ] **Step 4: Validate docker-compose syntax**

```bash
docker compose config --quiet
```

Expected: exits 0, no errors.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(api-gateway): add to docker-compose, fix serializer port conflict"
```

---

## Task 11: Final verification

- [ ] **Step 1: Run all Go tests**

```bash
cd services/api-gateway && go test ./... -v
```

Expected: all tests PASS, no failures.

- [ ] **Step 2: Verify health endpoint locally**

Start the binary with no downstream services running:

```bash
cd services/api-gateway && PORT=8090 UNDERSTANDER_URL=http://localhost:8086 RETRIEVER_ADDR=localhost:8087 EXPANDER_ADDR=localhost:8088 SERIALIZER_ADDR=localhost:8089 REDIS_URL=redis://localhost:6379 ./api-gateway &
sleep 1
curl -s http://localhost:8090/health
kill %1
```

Expected: `{"service":"api-gateway","status":"ok","version":"0.1.0"}`

- [ ] **Step 3: Verify rate limit header on 429**

With the binary running and a fast loop:

```bash
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST http://localhost:8090/query \
    -H 'Content-Type: application/json' \
    -d '{"repo":"test","question":"test"}'
done
```

Expected: first 10 lines are not 429 (likely 500 since no downstream), last 2 are 429.

- [ ] **Step 4: Commit final state**

```bash
git add -A
git commit -m "feat(api-gateway): complete implementation"
```
