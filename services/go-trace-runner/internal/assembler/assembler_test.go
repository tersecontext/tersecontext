package assembler

import (
	"testing"

	"github.com/tersecontext/tc/services/go-trace-runner/internal/collector"
)

func TestAssembleRawTrace(t *testing.T) {
	events := []collector.WireEvent{
		{Type: "call", Func: "auth.Login", File: "auth.go", Line: 10, TimestampMs: 0, GoroutineID: 1, SpanID: 1},
		{Type: "call", Func: "db.Query", File: "auth.go", Line: 20, TimestampMs: 5, GoroutineID: 1, SpanID: 2},
		{Type: "ret", File: "auth.go", Line: 20, TimestampMs: 18, GoroutineID: 1, SpanID: 2},
		{Type: "ret", File: "auth.go", Line: 10, TimestampMs: 25, GoroutineID: 1, SpanID: 1},
	}

	trace := Assemble("sha256:abc123", "def456", "myrepo", events)

	if trace.EntrypointStableID != "sha256:abc123" {
		t.Errorf("wrong stable_id: %s", trace.EntrypointStableID)
	}
	if len(trace.Events) != 4 {
		t.Fatalf("expected 4 events, got %d", len(trace.Events))
	}
	if trace.Events[2].Type != "return" {
		t.Errorf("expected 'return', got %s", trace.Events[2].Type)
	}
	if trace.DurationMs != 25 {
		t.Errorf("expected duration 25, got %f", trace.DurationMs)
	}
}

func TestAssemblePanicEvent(t *testing.T) {
	events := []collector.WireEvent{
		{Type: "call", Func: "handler.Create", File: "h.go", Line: 5, TimestampMs: 0, GoroutineID: 1, SpanID: 1},
		{Type: "panic", File: "h.go", Line: 12, TimestampMs: 10, GoroutineID: 1, SpanID: 1, ExcType: "nil pointer"},
	}

	trace := Assemble("sha256:xyz", "abc", "repo", events)

	if trace.Events[1].Type != "exception" {
		t.Errorf("expected 'exception', got %s", trace.Events[1].Type)
	}
	if trace.Events[1].ExcType != "nil pointer" {
		t.Errorf("expected 'nil pointer', got %s", trace.Events[1].ExcType)
	}
}
