package handlers

import (
	"encoding/json"
	"net/http"
	"sync"
	"time"

	"github.com/google/uuid"
)

type RunRequest struct {
	SessionID   string       `json:"session_id"`
	Entrypoints []Entrypoint `json:"entrypoints"`
	CommitSha   string       `json:"commit_sha"`
	Repo        string       `json:"repo"`
	TimeoutS    int          `json:"timeout_s"`
}

type Entrypoint struct {
	StableID string `json:"stable_id"`
	Name     string `json:"name"`
	Type     string `json:"type"`
}

type RunStatus struct {
	TraceID       string `json:"trace_id"`
	Status        string `json:"status"`
	EventsEmitted int    `json:"events_emitted,omitempty"`
	DurationMs    int64  `json:"duration_ms,omitempty"`
	Error         string `json:"error,omitempty"`
}

// Run accepts POST /run, validates the request, creates a trace ID,
// stores status in a sync.Map, and returns 202 Accepted.
func Run(store *sync.Map) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req RunRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, `{"error":"invalid request body"}`, http.StatusBadRequest)
			return
		}

		if req.SessionID == "" {
			http.Error(w, `{"error":"session_id is required"}`, http.StatusBadRequest)
			return
		}
		if len(req.Entrypoints) == 0 {
			http.Error(w, `{"error":"entrypoints must not be empty"}`, http.StatusBadRequest)
			return
		}
		if req.CommitSha == "" {
			http.Error(w, `{"error":"commit_sha is required"}`, http.StatusBadRequest)
			return
		}
		if req.Repo == "" {
			http.Error(w, `{"error":"repo is required"}`, http.StatusBadRequest)
			return
		}
		if req.TimeoutS <= 0 {
			req.TimeoutS = 30
		}

		traceID := uuid.New().String()
		status := &RunStatus{
			TraceID: traceID,
			Status:  "accepted",
		}
		store.Store(traceID, status)

		// Execution will be wired up in Task 9 with Redis.
		// For now, record accepted time and mark as running asynchronously.
		go func() {
			start := time.Now()
			_ = start
			// Placeholder: actual execution wired in Task 9.
		}()

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		json.NewEncoder(w).Encode(map[string]string{
			"trace_id": traceID,
			"status":   "accepted",
		})
	}
}

// RunStatusHandler serves GET /run/{id}/status.
func RunStatusHandler(store *sync.Map) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// r.PathValue is available in Go 1.22+ with the new ServeMux patterns.
		traceID := r.PathValue("id")

		if traceID == "" {
			http.Error(w, `{"error":"missing trace_id"}`, http.StatusBadRequest)
			return
		}

		val, ok := store.Load(traceID)
		if !ok {
			http.Error(w, `{"error":"trace not found"}`, http.StatusNotFound)
			return
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(val)
	}
}
