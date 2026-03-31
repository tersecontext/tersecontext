package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/tersecontext/tc/services/go-trace-runner/internal/handlers"
)

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8099"
	}

	sessionsDir := os.Getenv("SESSIONS_DIR")
	if sessionsDir == "" {
		sessionsDir = "/tmp/sessions"
	}

	var ready atomic.Bool
	var traceStore sync.Map

	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", handlers.Health)
	mux.HandleFunc("GET /ready", handlers.Ready(&ready))
	mux.HandleFunc("GET /metrics", handlers.Metrics)
	mux.HandleFunc("POST /run", handlers.Run(&traceStore, sessionsDir))
	mux.HandleFunc("GET /run/{id}/status", handlers.RunStatusHandler(&traceStore))

	srv := &http.Server{Addr: ":" + port, Handler: mux}

	// Graceful shutdown
	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
		<-sigCh
		ready.Store(false)
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		srv.Shutdown(ctx)
	}()

	ready.Store(true)
	log.Printf("go-trace-runner listening on :%s", port)
	if err := srv.ListenAndServe(); err != http.ErrServerClosed {
		log.Fatal(err)
	}
}
