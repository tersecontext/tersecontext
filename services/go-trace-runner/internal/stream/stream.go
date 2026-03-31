package stream

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/tersecontext/tc/services/go-trace-runner/internal/assembler"
)

const (
	StreamKey = "stream:raw-traces"
	CacheTTL  = 24 * time.Hour
)

func QueueKey(repo string) string {
	return fmt.Sprintf("entrypoint_queue:%s:go", repo)
}

func CacheKey(commitSha, stableID string) string {
	return fmt.Sprintf("trace_cache:%s:%s", commitSha, stableID)
}

func ToStreamFields(trace assembler.RawTrace) map[string]interface{} {
	data, _ := json.Marshal(trace)
	return map[string]interface{}{
		"entrypoint_stable_id": trace.EntrypointStableID,
		"repo":                 trace.Repo,
		"commit_sha":           trace.CommitSha,
		"data":                 string(data),
	}
}

func EmitRawTrace(ctx context.Context, rdb *redis.Client, trace assembler.RawTrace) error {
	return rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: StreamKey,
		Values: ToStreamFields(trace),
	}).Err()
}

func IsCached(ctx context.Context, rdb *redis.Client, commitSha, stableID string) bool {
	exists, _ := rdb.Exists(ctx, CacheKey(commitSha, stableID)).Result()
	return exists > 0
}

func MarkCached(ctx context.Context, rdb *redis.Client, commitSha, stableID string) error {
	return rdb.Set(ctx, CacheKey(commitSha, stableID), "1", CacheTTL).Err()
}

func ConsumeJobs(ctx context.Context, rdb *redis.Client, repos []string, timeout time.Duration) (string, error) {
	keys := make([]string, len(repos))
	for i, repo := range repos {
		keys[i] = QueueKey(repo)
	}
	result, err := rdb.BLPop(ctx, timeout, keys...).Result()
	if err != nil {
		return "", err
	}
	if len(result) < 2 {
		return "", fmt.Errorf("unexpected BLPOP result: %v", result)
	}
	return result[1], nil
}
