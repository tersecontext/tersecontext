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
	Repo     string       `json:"repo"`
	Question string       `json:"question"`
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

	ip, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil || ip == "" {
		ip = r.RemoteAddr
	}
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
