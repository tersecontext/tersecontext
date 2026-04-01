package handlers

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
	"github.com/tersecontext/tc/services/go-trace-runner/internal/assembler"
	"github.com/tersecontext/tc/services/go-trace-runner/internal/collector"
	"github.com/tersecontext/tc/services/go-trace-runner/internal/executor"
	"github.com/tersecontext/tc/services/go-trace-runner/internal/stream"
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

// Run accepts POST /run, validates the request, executes the instrumented
// binary, collects trace events, assembles RawTrace, and emits it to Redis.
func Run(store *sync.Map, sessionsDir string, rdb *redis.Client) http.HandlerFunc {
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

		// Execute the instrumented binary asynchronously
		go func() {
			start := time.Now()
			sessionDir := filepath.Join(sessionsDir, req.SessionID)
			binaryPath := filepath.Join(sessionDir, "binary")
			socketPath := filepath.Join(sessionDir, "trace.sock")

			// Check binary exists
			if _, err := os.Stat(binaryPath); os.IsNotExist(err) {
				status.Status = "failed"
				status.Error = "binary not found at " + binaryPath
				return
			}

			status.Status = "running"

			// Start socket listener in background
			eventsCh := make(chan []collector.WireEvent, 1)
			go func() {
				events, err := collector.ReadFromSocket(socketPath)
				if err != nil {
					log.Printf("collector error: %v", err)
					eventsCh <- nil
					return
				}
				eventsCh <- events
			}()

			// Give socket listener time to start
			time.Sleep(50 * time.Millisecond)

			// Execute binary
			exec := executor.New(sessionDir)
			exec.CreateLock()
			defer exec.ReleaseLock()

			timeout := exec.Timeout(req.Entrypoints[0].Type, req.TimeoutS)

			// Build test pattern from entrypoint names
			testPattern := ""
			if req.Entrypoints[0].Type == "test" {
				testPattern = req.Entrypoints[0].Name
			}

			err := exec.RunTestBinary(context.Background(), binaryPath, testPattern, timeout, socketPath)
			if err != nil {
				log.Printf("execution error: %v", err)
				status.Error = err.Error()
			}

			// Collect events
			events := <-eventsCh

			if events != nil {
				for _, ep := range req.Entrypoints {
					trace := assembler.Assemble(ep.StableID, req.CommitSha, req.Repo, events)
					status.EventsEmitted += len(trace.Events)
					if emitErr := stream.EmitRawTrace(context.Background(), rdb, trace); emitErr != nil {
						log.Printf("stream emit error for %s: %v", ep.StableID, emitErr)
					}
				}
			}

			status.DurationMs = time.Since(start).Milliseconds()
			if status.Error == "" {
				status.Status = "completed"
			} else {
				status.Status = "failed"
			}
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
