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

// TraceIDExportedKey exposes the context key so test packages can inject trace IDs.
var TraceIDExportedKey = traceIDKey
