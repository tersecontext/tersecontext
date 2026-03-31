package handlers

import (
	"encoding/json"
	"net/http"
	"sync/atomic"
)

func Health(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"status":  "ok",
		"service": "go-trace-runner",
		"version": "0.1.0",
	})
}

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

func Metrics(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
}
