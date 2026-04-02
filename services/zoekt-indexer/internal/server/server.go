package server

import (
	"encoding/json"
	"net/http"
	"sync/atomic"

	"github.com/tersecontext/tc/services/zoekt-indexer/internal/indexer"
)

// Server exposes /health, /ready, /metrics, POST /index.
type Server struct {
	mux   *http.ServeMux
	ready *atomic.Bool
	idx   *indexer.Indexer
}

func New(ready *atomic.Bool, idx *indexer.Indexer) *Server {
	s := &Server{mux: http.NewServeMux(), ready: ready, idx: idx}
	s.mux.HandleFunc("GET /health", s.health)
	s.mux.HandleFunc("GET /ready", s.readiness)
	s.mux.HandleFunc("GET /metrics", s.metrics)
	s.mux.HandleFunc("POST /index", s.index)
	return s
}

func (s *Server) Handler() http.Handler { return s.mux }

func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

func (s *Server) readiness(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	if s.ready.Load() {
		json.NewEncoder(w).Encode(map[string]string{"status": "ready"})
	} else {
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]string{"status": "not ready"})
	}
}

func (s *Server) metrics(w http.ResponseWriter, _ *http.Request) {
	w.WriteHeader(http.StatusOK)
}

func (s *Server) index(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Repo string `json:"repo"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Repo == "" {
		http.Error(w, "repo required", http.StatusBadRequest)
		return
	}
	if err := s.idx.IndexRepo(req.Repo); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "indexed"})
}
