package stream

import (
	"testing"

	"github.com/tersecontext/tc/services/go-trace-runner/internal/assembler"
)

func TestTraceKeyFormat(t *testing.T) {
	key := CacheKey("abc123", "sha256:def456")
	expected := "trace_cache:abc123:sha256:def456"
	if key != expected {
		t.Errorf("expected %q, got %q", expected, key)
	}
}

func TestQueueKey(t *testing.T) {
	key := QueueKey("gastown")
	expected := "entrypoint_queue:gastown:go"
	if key != expected {
		t.Errorf("expected %q, got %q", expected, key)
	}
}

func TestRawTraceToStreamFields(t *testing.T) {
	trace := assembler.RawTrace{
		EntrypointStableID: "sha256:abc",
		CommitSha:          "def",
		Repo:               "repo",
		DurationMs:         100,
		Events: []assembler.TraceEvent{
			{Type: "call", Fn: "main.A", File: "a.go", Line: 1, TimestampMs: 0},
		},
	}

	fields := ToStreamFields(trace)
	if fields["entrypoint_stable_id"] != "sha256:abc" {
		t.Error("missing entrypoint_stable_id")
	}
	if fields["data"] == nil || fields["data"] == "" {
		t.Error("missing data field")
	}
}
