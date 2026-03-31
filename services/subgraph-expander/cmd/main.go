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

	querypb "github.com/tersecontext/tc/services/subgraph-expander/gen"
	"github.com/tersecontext/tc/services/subgraph-expander/internal/expander"
	neo4jclient "github.com/tersecontext/tc/services/subgraph-expander/internal/neo4j"
	pgclient "github.com/tersecontext/tc/services/subgraph-expander/internal/postgres"
	"github.com/tersecontext/tc/services/subgraph-expander/internal/server"
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

func envFloat(key string, fallback float64) float64 {
	if v := os.Getenv(key); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return fallback
}

func envInt(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if i, err := strconv.Atoi(v); err == nil {
			return i
		}
	}
	return fallback
}

func main() {
	grpcPort := env("GRPC_PORT", "8088")
	neo4jURI := env("NEO4J_URI", "bolt://neo4j:7687")
	neo4jUser := env("NEO4J_USER", "neo4j")
	neo4jPassword := os.Getenv("NEO4J_PASSWORD")
	if neo4jPassword == "" {
		slog.Error("NEO4J_PASSWORD environment variable is required")
		os.Exit(1)
	}
	postgresDSN := env("POSTGRES_DSN", "")
	hopDepth := envInt("EXPANDER_HOP_DEPTH", 2)
	decay := envFloat("EXPANDER_DECAY", 0.7)

	ctx := context.Background()
	var ready atomic.Bool

	// --- Neo4j ---
	neo4jClient, err := neo4jclient.New(ctx, neo4jURI, neo4jUser, neo4jPassword)
	if err != nil {
		slog.Error("failed to connect to neo4j", "error", err)
		os.Exit(1)
	}
	defer neo4jClient.Close(ctx)

	// --- Postgres ---
	var pgClient *pgclient.Client
	if postgresDSN != "" {
		pgClient, err = pgclient.New(ctx, postgresDSN)
		if err != nil {
			slog.Error("failed to connect to postgres", "error", err)
			os.Exit(1)
		}
		defer pgClient.Close()
	} else {
		slog.Warn("POSTGRES_DSN not set; behavior spec lookups will be skipped")
	}

	// --- Build expander ---
	var specChecker expander.SpecChecker = &noopSpecChecker{}
	if pgClient != nil {
		specChecker = pgClient
	}
	exp := expander.New(neo4jClient, specChecker, neo4jClient)

	// --- gRPC server ---
	lis, err := net.Listen("tcp", ":"+grpcPort)
	if err != nil {
		slog.Error("failed to listen", "port", grpcPort, "error", err)
		os.Exit(1)
	}

	grpcServer := grpc.NewServer()
	querypb.RegisterQueryServiceServer(grpcServer, server.NewQueryServer(exp, hopDepth, decay))

	healthServer := health.NewServer()
	healthpb.RegisterHealthServer(grpcServer, healthServer)

	ready.Store(true)
	healthServer.SetServingStatus("", healthpb.HealthCheckResponse_SERVING)

	// --- HTTP health server ---
	httpPort, _ := strconv.Atoi(grpcPort)
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

	slog.Info("gRPC server starting", "port", grpcPort)
	if err := grpcServer.Serve(lis); err != nil {
		slog.Error("gRPC server error", "error", err)
		os.Exit(1)
	}
}

// noopSpecChecker is used when Postgres is not configured.
type noopSpecChecker struct{}

func (n *noopSpecChecker) FetchBehaviorSpecs(_ context.Context, _ []string) (map[string]string, error) {
	return map[string]string{}, nil
}
