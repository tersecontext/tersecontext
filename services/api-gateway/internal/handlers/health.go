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
