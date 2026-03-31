package tracert

import (
	"fmt"
	"sync"
	"sync/atomic"
	"time"
)

// Event represents a single trace event.
type Event struct {
	Type        string  `json:"t"`
	Func        string  `json:"f"`
	File        string  `json:"file,omitempty"`
	Line        int     `json:"l,omitempty"`
	TimestampMs float64 `json:"ts"`
	GoroutineID uint64  `json:"g"`
	SpanID      uint64  `json:"s"`
	Args        any     `json:"args,omitempty"`
	ReturnVal   any     `json:"ret,omitempty"`
	ExcType     string  `json:"exc,omitempty"`
}

var (
	startTime   time.Time
	nextSpanID  atomic.Uint64
	initialized atomic.Bool
	testMode    atomic.Bool
	testEvents  []Event
	testMu      sync.Mutex
)

// Init opens the Unix socket connection for event emission.
func Init(socketPath string) {
	startTime = time.Now()
	nextSpanID.Store(1)
	initialized.Store(true)
	initEmitter(socketPath)
}

// initTestMode sets up tracert for testing without a socket.
func initTestMode() {
	startTime = time.Now()
	nextSpanID.Store(1)
	initialized.Store(true)
	testMode.Store(true)
	testMu.Lock()
	testEvents = nil
	testMu.Unlock()
	resetGoroutineState()
}

// drainTestEvents returns and clears all test events.
func drainTestEvents() []Event {
	testMu.Lock()
	defer testMu.Unlock()
	events := testEvents
	testEvents = nil
	return events
}

func emit(e Event) {
	if testMode.Load() {
		testMu.Lock()
		testEvents = append(testEvents, e)
		testMu.Unlock()
		return
	}
	if initialized.Load() {
		emitEvent(e)
	}
}

func elapsedMs() float64 {
	return float64(time.Since(startTime).Microseconds()) / 1000.0
}

// Enter records a function call event. Returns a span ID for Exit.
func Enter(funcID string, args ...any) uint64 {
	spanID := nextSpanID.Add(1) - 1
	ctx := currentGoroutineCtx()

	e := Event{
		Type:        "call",
		Func:        funcID,
		TimestampMs: elapsedMs(),
		GoroutineID: ctx.id,
		SpanID:      spanID,
	}
	if len(args) > 0 {
		e.Args = args
	}
	emit(e)
	return spanID
}

// Exit records a function return event.
func Exit(spanID uint64, returns ...any) {
	ctx := currentGoroutineCtx()

	e := Event{
		Type:        "ret",
		Func:        "",
		TimestampMs: elapsedMs(),
		GoroutineID: ctx.id,
		SpanID:      spanID,
	}
	if len(returns) > 0 {
		e.ReturnVal = returns
	}
	emit(e)
}

// ExitPanic records a panic event. Called from the deferred closure when
// recover() catches a panic. The closure must call recover() directly
// (Go constraint: recover only works in the directly deferred function).
//
// Usage in AST-rewritten code:
//
//	defer func() {
//	    if r := recover(); r != nil {
//	        tracert.ExitPanic(__span, r)
//	        panic(r)
//	    }
//	    tracert.Exit(__span)
//	}()
func ExitPanic(spanID uint64, panicVal any) {
	ctx := currentGoroutineCtx()

	e := Event{
		Type:        "panic",
		Func:        "",
		TimestampMs: elapsedMs(),
		GoroutineID: ctx.id,
		SpanID:      spanID,
		ExcType:     fmt.Sprintf("%v", panicVal),
	}
	emit(e)
}

// Go wraps goroutine creation to propagate trace context.
func Go(fn func()) {
	ctx := currentGoroutineCtx()
	parentID := ctx.id
	go func() {
		registerChildGoroutine(parentID)
		fn()
	}()
}

// Flush drains all buffered events to the socket.
func Flush() {
	if initialized.Load() && !testMode.Load() {
		flushEmitter()
	}
}
