package tracert

import (
	"testing"
)

func TestEnterExitProducesCallReturnEvents(t *testing.T) {
	initTestMode()

	span := Enter("pkg.FuncA")
	Exit(span)

	events := drainTestEvents()
	if len(events) != 2 {
		t.Fatalf("expected 2 events, got %d", len(events))
	}
	if events[0].Type != "call" {
		t.Errorf("expected call event, got %s", events[0].Type)
	}
	if events[0].Func != "pkg.FuncA" {
		t.Errorf("expected pkg.FuncA, got %s", events[0].Func)
	}
	if events[1].Type != "ret" {
		t.Errorf("expected ret event, got %s", events[1].Type)
	}
	if events[1].SpanID != events[0].SpanID {
		t.Errorf("span IDs should match: %d != %d", events[1].SpanID, events[0].SpanID)
	}
}

func TestNestedCallsProduceCorrectOrder(t *testing.T) {
	initTestMode()

	spanA := Enter("pkg.A")
	spanB := Enter("pkg.B")
	Exit(spanB)
	Exit(spanA)

	events := drainTestEvents()
	if len(events) != 4 {
		t.Fatalf("expected 4 events, got %d", len(events))
	}
	expected := []string{"call", "call", "ret", "ret"}
	for i, e := range events {
		if e.Type != expected[i] {
			t.Errorf("event %d: expected %s, got %s", i, expected[i], e.Type)
		}
	}
}

func TestExitPanicEmitsPanicEvent(t *testing.T) {
	initTestMode()

	defer func() {
		r := recover()
		if r == nil {
			t.Fatal("expected panic to propagate")
		}
		events := drainTestEvents()
		found := false
		for _, e := range events {
			if e.Type == "panic" {
				found = true
				if e.ExcType != "test panic" {
					t.Errorf("expected 'test panic', got %s", e.ExcType)
				}
			}
		}
		if !found {
			t.Error("expected panic event")
		}
	}()

	span := Enter("pkg.Panicker")
	// This mirrors the AST-rewritten defer pattern:
	// recover() must be called in the directly deferred function.
	defer func() {
		if r := recover(); r != nil {
			ExitPanic(span, r)
			panic(r)
		}
		Exit(span)
	}()
	panic("test panic")
}
