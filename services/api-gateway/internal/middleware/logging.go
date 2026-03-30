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
