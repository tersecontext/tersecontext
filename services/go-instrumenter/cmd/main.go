package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/tersecontext/tc/services/go-instrumenter/internal/handlers"
	"github.com/tersecontext/tc/services/go-instrumenter/internal/session"
)

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8098"
	}
	sessionsDir := os.Getenv("SESSIONS_DIR")
	if sessionsDir == "" {
		sessionsDir = "/tmp/sessions"
	}

	sessMgr := session.NewManager(sessionsDir)
	var ready atomic.Bool

	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", handlers.Health)
	mux.HandleFunc("GET /ready", handlers.Ready(&ready))
	mux.HandleFunc("GET /metrics", handlers.Metrics)
	// POST /instrument will be added in Task 6

	srv := &http.Server{Addr: ":" + port, Handler: mux}

	// Start cleanup ticker
	go func() {
		ticker := time.NewTicker(10 * time.Minute)
		defer ticker.Stop()
		for range ticker.C {
			sessMgr.CleanupExpired(1 * time.Hour)
		}
	}()

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
	log.Printf("go-instrumenter listening on :%s", port)
	if err := srv.ListenAndServe(); err != http.ErrServerClosed {
		log.Fatal(err)
	}
}
