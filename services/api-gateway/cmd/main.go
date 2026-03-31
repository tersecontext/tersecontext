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
