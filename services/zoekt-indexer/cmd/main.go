package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/tersecontext/tc/services/zoekt-indexer/internal/indexer"
	"github.com/tersecontext/tc/services/zoekt-indexer/internal/server"
)

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

const (
	streamRepoIndexed = "stream:repo-indexed"
	streamFileChanged = "stream:file-changed"
	groupRepoIndexed  = "zoekt-indexer-group"
	groupFileChanged  = "zoekt-file-watcher-group"
	debounceWindow    = 100 * time.Millisecond
)

func main() {
	port := env("PORT", "8099")
	reposDir := env("REPOS_DIR", "/repos")
	indexDir := env("ZOEKT_INDEX_DIR", "/data/index")
	zoektURL := env("ZOEKT_URL", "http://zoekt-webserver:6070")
	redisURL := env("REDIS_URL", "redis://redis:6379")

	idx := indexer.NewIndexer(indexDir, reposDir)
	deb := indexer.NewDebouncer(debounceWindow)

	var ready atomic.Bool

	// Readiness: check zoekt-webserver is reachable.
	go func() {
		for {
			resp, err := http.Get(zoektURL + "/healthz")
			if err == nil && resp.StatusCode == http.StatusOK {
				ready.Store(true)
				slog.Info("zoekt-webserver is reachable")
				return
			}
			slog.Info("waiting for zoekt-webserver", "url", zoektURL)
			time.Sleep(2 * time.Second)
		}
	}()

	rdbOpts, err := redis.ParseURL(redisURL)
	if err != nil {
		slog.Error("invalid REDIS_URL", "url", redisURL, "error", err)
		os.Exit(1)
	}
	rdb := redis.NewClient(rdbOpts)
	ctx, cancel := context.WithCancel(context.Background())

	// Create consumer groups (BUSYGROUP is OK).
	for _, pair := range [][2]string{
		{streamRepoIndexed, groupRepoIndexed},
		{streamFileChanged, groupFileChanged},
	} {
		if err := rdb.XGroupCreateMkStream(ctx, pair[0], pair[1], "$").Err(); err != nil {
			if err.Error() != "BUSYGROUP Consumer Group name already exists" {
				slog.Warn("xgroup create", "stream", pair[0], "error", err)
			}
		}
	}

	// Consumer: stream:repo-indexed → full re-index.
	go func() {
		for {
			msgs, err := rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
				Group: groupRepoIndexed, Consumer: "zoekt-indexer",
				Streams: []string{streamRepoIndexed, ">"},
				Count: 10, Block: time.Second,
			}).Result()
			if err != nil && ctx.Err() == nil {
				slog.Warn("xreadgroup repo-indexed", "error", err)
				continue
			}
			for _, msg := range msgs {
				for _, m := range msg.Messages {
					repo, _ := m.Values["repo"].(string)
					if repo != "" {
						slog.Info("full re-index", "repo", repo)
						if err := idx.IndexRepo(repo); err != nil {
							slog.Error("index repo", "repo", repo, "error", err)
						}
					}
					rdb.XAck(ctx, streamRepoIndexed, groupRepoIndexed, m.ID)
				}
			}
		}
	}()

	// Consumer: stream:file-changed → debounced re-index.
	go func() {
		for {
			msgs, err := rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
				Group: groupFileChanged, Consumer: "zoekt-indexer",
				Streams: []string{streamFileChanged, ">"},
				Count: 10, Block: time.Second,
			}).Result()
			if err != nil && ctx.Err() == nil {
				slog.Warn("xreadgroup file-changed", "error", err)
				continue
			}
			for _, msg := range msgs {
				for _, m := range msg.Messages {
					repo, _ := m.Values["repo"].(string)
					if repo != "" {
						deb.Trigger(repo, func(r string) {
							slog.Info("debounced re-index", "repo", r)
							if err := idx.IndexRepo(r); err != nil {
								slog.Error("index repo", "repo", r, "error", err)
							}
						})
					}
					rdb.XAck(ctx, streamFileChanged, groupFileChanged, m.ID)
				}
			}
		}
	}()

	srv := server.New(&ready, idx)
	httpServer := &http.Server{Addr: ":" + port, Handler: srv.Handler()}

	go func() {
		slog.Info("HTTP server starting", "port", port)
		if err := httpServer.ListenAndServe(); err != http.ErrServerClosed {
			slog.Error("HTTP server", "error", err)
		}
	}()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	<-sigCh
	slog.Info("shutting down")
	cancel()
	httpServer.Shutdown(context.Background())
}
