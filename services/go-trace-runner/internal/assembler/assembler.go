package assembler

import (
	"github.com/tersecontext/tc/services/go-trace-runner/internal/collector"
)

type TraceEvent struct {
	Type        string  `json:"type"`
	Fn          string  `json:"fn"`
	File        string  `json:"file"`
	Line        int     `json:"line"`
	TimestampMs float64 `json:"timestamp_ms"`
	ExcType     string  `json:"exc_type,omitempty"`
	GoroutineID uint64  `json:"goroutine_id,omitempty"`
	SpanID      uint64  `json:"span_id,omitempty"`
	Args        any     `json:"args,omitempty"`
	ReturnVal   any     `json:"return_val,omitempty"`
}

type RawTrace struct {
	EntrypointStableID string       `json:"entrypoint_stable_id"`
	CommitSha          string       `json:"commit_sha"`
	Repo               string       `json:"repo"`
	DurationMs         float64      `json:"duration_ms"`
	Events             []TraceEvent `json:"events"`
}

func Assemble(stableID, commitSha, repo string, events []collector.WireEvent) RawTrace {
	traceEvents := make([]TraceEvent, len(events))
	var maxTs float64

	for i, we := range events {
		traceEvents[i] = TraceEvent{
			Type:        collector.MapEventType(we.Type),
			Fn:          we.Func,
			File:        we.File,
			Line:        we.Line,
			TimestampMs: we.TimestampMs,
			ExcType:     we.ExcType,
			GoroutineID: we.GoroutineID,
			SpanID:      we.SpanID,
			Args:        we.Args,
			ReturnVal:   we.ReturnVal,
		}
		if we.TimestampMs > maxTs {
			maxTs = we.TimestampMs
		}
	}

	return RawTrace{
		EntrypointStableID: stableID,
		CommitSha:          commitSha,
		Repo:               repo,
		DurationMs:         maxTs,
		Events:             traceEvents,
	}
}
