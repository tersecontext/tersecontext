package handlers

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"

	"github.com/redis/go-redis/v9"
)

// Scanner is the minimal Redis interface the repos handler needs.
type Scanner interface {
	Scan(ctx context.Context, cursor uint64, match string, count int64) *redis.ScanCmd
}

// Repos handles GET /repos — returns the list of indexed repo names.
func Repos(client Scanner) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ctx := r.Context()
		var repos []string
		var cursor uint64

		for {
			keys, next, err := client.Scan(ctx, cursor, "last_indexed_sha:*", 100).Result()
			if err != nil {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusInternalServerError)
				json.NewEncoder(w).Encode(map[string]string{"error": "failed to scan repos"})
				return
			}
			for _, k := range keys {
				repo := strings.TrimPrefix(k, "last_indexed_sha:")
				repos = append(repos, repo)
			}
			cursor = next
			if cursor == 0 {
				break
			}
		}

		if repos == nil {
			repos = []string{}
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(repos)
	}
}
