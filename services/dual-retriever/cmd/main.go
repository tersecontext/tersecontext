package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"sync/atomic"
	"syscall"

	"github.com/neo4j/neo4j-go-driver/v5/neo4j"
	querypb "github.com/tersecontext/tc/services/dual-retriever/gen"
	"github.com/tersecontext/tc/services/dual-retriever/internal/retriever"
	"github.com/tersecontext/tc/services/dual-retriever/internal/server"
	"google.golang.org/grpc"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
)

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func main() {
	port := env("PORT", "8087")
	neo4jURL := env("NEO4J_URL", "bolt://neo4j:7687")
	neo4jUser := env("NEO4J_USER", "neo4j")
	neo4jPassword := env("NEO4J_PASSWORD", "")
	qdrantURL := env("QDRANT_URL", "http://qdrant:6333")
	embedderURL := env("EMBEDDER_URL", "http://embedder:8080")

	ctx := context.Background()
	var ready atomic.Bool

	// --- Neo4j ---
	neo4jDriver, err := neo4j.NewDriverWithContext(neo4jURL, neo4j.BasicAuth(neo4jUser, neo4jPassword, ""))
	if err != nil {
		slog.Error("failed to create neo4j driver", "error", err)
		os.Exit(1)
	}
	defer neo4jDriver.Close(ctx)

	// --- Build retriever ---
	embedder := retriever.NewHTTPEmbedder(embedderURL, nil)
	vectorSearcher := retriever.NewQdrantSearcher(qdrantURL, "nodes", nil)
	graphSearcher := retriever.NewNeo4jSearcher(neo4jDriver)
	ret := retriever.NewRetriever(embedder, vectorSearcher, graphSearcher)

	// --- gRPC server ---
	lis, err := net.Listen("tcp", ":"+port)
	if err != nil {
		slog.Error("failed to listen", "port", port, "error", err)
		os.Exit(1)
	}

	grpcServer := grpc.NewServer()
	querypb.RegisterQueryServiceServer(grpcServer, server.NewQueryServer(ret))

	healthServer := health.NewServer()
	healthpb.RegisterHealthServer(grpcServer, healthServer)

	ready.Store(true)
	healthServer.SetServingStatus("", healthpb.HealthCheckResponse_SERVING)

	// --- HTTP health server ---
	httpPort, _ := strconv.Atoi(port)
	httpAddr := fmt.Sprintf(":%d", httpPort+1)

	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	})
	mux.HandleFunc("GET /ready", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if ready.Load() {
			json.NewEncoder(w).Encode(map[string]string{"status": "ready"})
		} else {
			w.WriteHeader(http.StatusServiceUnavailable)
			json.NewEncoder(w).Encode(map[string]string{"status": "not ready"})
		}
	})
	mux.HandleFunc("GET /metrics", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})

	httpServer := &http.Server{Addr: httpAddr, Handler: mux}

	go func() {
		slog.Info("HTTP health server starting", "addr", httpAddr)
		if err := httpServer.ListenAndServe(); err != http.ErrServerClosed {
			slog.Error("HTTP server error", "error", err)
		}
	}()

	// --- Graceful shutdown ---
	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
		sig := <-sigCh
		slog.Info("shutting down", "signal", sig)
		healthServer.SetServingStatus("", healthpb.HealthCheckResponse_NOT_SERVING)
		grpcServer.GracefulStop()
		httpServer.Shutdown(ctx)
	}()

	slog.Info("gRPC server starting", "port", port)
	if err := grpcServer.Serve(lis); err != nil {
		slog.Error("gRPC server error", "error", err)
		os.Exit(1)
	}
}
