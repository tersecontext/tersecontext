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
