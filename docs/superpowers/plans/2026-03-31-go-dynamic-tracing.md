# Go Dynamic Tracing Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Go runtime tracing via coverage-guided AST source rewriting — producing RawTrace events compatible with the existing downstream pipeline.

**Architecture:** Three new Go components (tracert library, go-instrumenter service at 8098, go-trace-runner service at 8099) plus minor modifications to the Python entrypoint-discoverer. The tracert library is injected into target binaries at compile time. The go-instrumenter rewrites Go source with tracing hooks and builds the binary. The go-trace-runner executes it, collects events over a Unix socket, and emits RawTrace to `stream:raw-traces`.

**Tech Stack:** Go 1.24, `go/ast` + `go/parser` + `go/printer`, `github.com/petermattis/goid`, `github.com/redis/go-redis/v9`, Unix domain sockets, JSON lines. Python modifications use FastAPI + Pydantic.

**Spec:** `docs/superpowers/specs/2026-03-31-go-dynamic-tracing-design.md`

---

## File Map

### tracert (Go library — injected into instrumented binaries)

| File | Responsibility |
|------|----------------|
| `services/tracert/go.mod` | Module definition |
| `services/tracert/tracert.go` | Public API: `Enter`, `Exit`, `Go`, `Init`, `Flush` |
| `services/tracert/goroutine.go` | Per-goroutine context tracking via `goid` + `sync.Map` |
| `services/tracert/buffer.go` | Pre-allocated ring buffer for event batching |
| `services/tracert/emitter.go` | Unix socket writer, JSON line serialization |
| `services/tracert/tracert_test.go` | Unit tests: call stack, goroutine tracking, event format |
| `services/tracert/buffer_test.go` | Ring buffer overflow, concurrent write tests |
| `services/tracert/emitter_test.go` | Socket emission, JSON format tests |

### go-instrumenter (Go service at port 8098)

| File | Responsibility |
|------|----------------|
| `services/go-instrumenter/go.mod` | Module definition |
| `services/go-instrumenter/cmd/main.go` | HTTP server entry point, graceful shutdown |
| `services/go-instrumenter/internal/handlers/health.go` | `/health`, `/ready`, `/metrics` endpoints |
| `services/go-instrumenter/internal/handlers/instrument.go` | `POST /instrument` handler |
| `services/go-instrumenter/internal/rewriter/rewriter.go` | AST parse + rewrite: inject `Enter`/`Exit`/`Go` calls |
| `services/go-instrumenter/internal/rewriter/patterns.go` | Boundary function pattern matching |
| `services/go-instrumenter/internal/rewriter/rewriter_test.go` | AST rewriting tests per function type |
| `services/go-instrumenter/internal/rewriter/patterns_test.go` | Pattern matching tests |
| `services/go-instrumenter/internal/builder/builder.go` | `go mod edit` + `go build` / `go test -c` |
| `services/go-instrumenter/internal/builder/builder_test.go` | Build pipeline tests |
| `services/go-instrumenter/internal/coverage/coverage.go` | `go test -coverprofile` runner + parser |
| `services/go-instrumenter/internal/coverage/coverage_test.go` | Coverage parsing tests |
| `services/go-instrumenter/internal/session/session.go` | Session directory management, TTL cleanup |
| `services/go-instrumenter/internal/session/session_test.go` | Session lifecycle tests |
| `services/go-instrumenter/Dockerfile` | Container build |

### go-trace-runner (Go service at port 8099)

| File | Responsibility |
|------|----------------|
| `services/go-trace-runner/go.mod` | Module definition |
| `services/go-trace-runner/cmd/main.go` | HTTP server + Redis queue consumer, graceful shutdown |
| `services/go-trace-runner/internal/handlers/health.go` | `/health`, `/ready`, `/metrics` endpoints |
| `services/go-trace-runner/internal/handlers/run.go` | `POST /run`, `GET /run/{id}/status` handlers |
| `services/go-trace-runner/internal/collector/collector.go` | Unix socket reader, event grouping by goroutine |
| `services/go-trace-runner/internal/collector/collector_test.go` | Event parsing, grouping, mapping tests |
| `services/go-trace-runner/internal/assembler/assembler.go` | RawTrace assembly from collected events |
| `services/go-trace-runner/internal/assembler/assembler_test.go` | RawTrace schema conformance tests |
| `services/go-trace-runner/internal/executor/executor.go` | Binary execution, timeout, lock files |
| `services/go-trace-runner/internal/executor/executor_test.go` | Execution lifecycle tests |
| `services/go-trace-runner/internal/stream/stream.go` | Redis XADD emission + BLPOP consumer + trace cache |
| `services/go-trace-runner/internal/stream/stream_test.go` | Redis integration tests |
| `services/go-trace-runner/Dockerfile` | Container build |

> **Deferred to follow-up plan:** Synthetic request generation (`internal/synthetic/`) and Go parser framework annotation detection (`extractor_go.py`). These correspond to spec Phases 5 and 6. The PoC targets Phases 1-4 (test-driven tracing). Framework-based entrypoint discovery requires the parser extension as a prerequisite.

### Modified files

| File | Change |
|------|--------|
| `services/entrypoint-discoverer/app/models.py` | Add `language: str = "python"` to `EntrypointJob` |
| `services/entrypoint-discoverer/app/discoverer.py` | Add Go entrypoint Cypher patterns |
| `services/entrypoint-discoverer/app/queue.py` | Route Go jobs to `entrypoint_queue:{repo}:go` |
| `services/entrypoint-discoverer/tests/test_discoverer.py` | Tests for Go entrypoint detection |
| `services/trace-runner/app/models.py` | Add optional `goroutine_id`, `span_id`, `args`, `return_val` to `TraceEvent` |
| `docker-compose.yml` | Add `go-instrumenter` and `go-trace-runner` services |

---

## Task 1: Tracert Library — Core API and Per-Goroutine Tracking

**Files:**
- Create: `services/tracert/go.mod`
- Create: `services/tracert/tracert.go`
- Create: `services/tracert/goroutine.go`
- Create: `services/tracert/tracert_test.go`

- [ ] **Step 1: Create go.mod**

```
services/tracert/go.mod
```
```
module github.com/tersecontext/tc/services/tracert

go 1.24.0

require github.com/petermattis/goid v0.1.2
```

- [ ] **Step 2: Write failing test for Enter/Exit call stack**

```
services/tracert/tracert_test.go
```
```go
package tracert

import (
	"testing"
)

func TestEnterExitProducesCallReturnEvents(t *testing.T) {
	// Init with no socket (buffer-only mode for testing)
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
	// call A, call B, ret B, ret A
	expected := []string{"call", "call", "ret", "ret"}
	for i, e := range events {
		if e.Type != expected[i] {
			t.Errorf("event %d: expected %s, got %s", i, expected[i], e.Type)
		}
	}
}

func TestExitDetectsPanic(t *testing.T) {
	initTestMode()

	defer func() {
		r := recover()
		if r == nil {
			t.Fatal("expected panic to propagate")
		}
		events := drainTestEvents()
		// Should have: call, panic
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
	defer func() { Exit(span) }()
	panic("test panic")
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services/tracert && go test -v -run TestEnterExit`
Expected: FAIL — types and functions not defined

- [ ] **Step 4: Implement goroutine.go — per-goroutine context**

```
services/tracert/goroutine.go
```
```go
package tracert

import (
	"sync"
	"sync/atomic"

	"github.com/petermattis/goid"
)

// goroutineCtx holds per-goroutine tracing state.
type goroutineCtx struct {
	id       uint64 // trace-local goroutine ID
	parentID uint64 // parent goroutine's trace-local ID
}

var (
	goroutineMap   sync.Map       // goid → *goroutineCtx
	nextGoroutineID atomic.Uint64
)

func init() {
	nextGoroutineID.Store(1)
}

// currentGoroutineCtx returns or creates the context for the current goroutine.
func currentGoroutineCtx() *goroutineCtx {
	gid := goid.Get()
	if ctx, ok := goroutineMap.Load(gid); ok {
		return ctx.(*goroutineCtx)
	}
	ctx := &goroutineCtx{
		id: nextGoroutineID.Add(1) - 1,
	}
	goroutineMap.Store(gid, ctx)
	return ctx
}

// registerChildGoroutine sets up tracing context for a new goroutine,
// inheriting the parent's trace-local ID.
func registerChildGoroutine(parentTraceID uint64) {
	gid := goid.Get()
	ctx := &goroutineCtx{
		id:       nextGoroutineID.Add(1) - 1,
		parentID: parentTraceID,
	}
	goroutineMap.Store(gid, ctx)
}

// resetGoroutineState clears all goroutine tracking (for testing).
func resetGoroutineState() {
	goroutineMap.Range(func(key, _ any) bool {
		goroutineMap.Delete(key)
		return true
	})
	nextGoroutineID.Store(1)
}
```

- [ ] **Step 5: Implement tracert.go — Enter, Exit, Go, Init, Flush**

```
services/tracert/tracert.go
```
```go
package tracert

import (
	"fmt"
	"sync"
	"sync/atomic"
	"time"
)

// Event represents a single trace event.
type Event struct {
	Type        string `json:"t"`
	Func        string `json:"f"`
	File        string `json:"file,omitempty"`
	Line        int    `json:"l,omitempty"`
	TimestampMs float64 `json:"ts"`
	GoroutineID uint64 `json:"g"`
	SpanID      uint64 `json:"s"`
	Args        any    `json:"args,omitempty"`
	ReturnVal   any    `json:"ret,omitempty"`
	ExcType     string `json:"exc,omitempty"`
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

// Exit records a function return event. Detects panics via recover().
func Exit(spanID uint64, returns ...any) {
	ctx := currentGoroutineCtx()

	// Check for panic
	if r := recover(); r != nil {
		e := Event{
			Type:        "panic",
			Func:        "", // will be filled from span context if needed
			TimestampMs: elapsedMs(),
			GoroutineID: ctx.id,
			SpanID:      spanID,
			ExcType:     fmt.Sprintf("%v", r),
		}
		emit(e)
		panic(r) // re-panic to preserve stack
	}

	e := Event{
		Type:        "ret",
		Func:        "", // collector matches via span ID
		TimestampMs: elapsedMs(),
		GoroutineID: ctx.id,
		SpanID:      spanID,
	}
	if len(returns) > 0 {
		e.ReturnVal = returns
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
```

- [ ] **Step 6: Run tests**

Run: `cd services/tracert && go mod tidy && go test -v`
Expected: All 3 tests pass

- [ ] **Step 7: Commit**

```bash
git add services/tracert/
git commit -m "feat(tracert): core tracing library with Enter/Exit/Go and per-goroutine tracking"
```

---

## Task 2: Tracert Library — Ring Buffer and Unix Socket Emitter

**Files:**
- Create: `services/tracert/buffer.go`
- Create: `services/tracert/emitter.go`
- Create: `services/tracert/buffer_test.go`
- Create: `services/tracert/emitter_test.go`

- [ ] **Step 1: Write failing test for ring buffer**

```
services/tracert/buffer_test.go
```
```go
package tracert

import (
	"fmt"
	"sync"
	"testing"
)

func TestRingBufferWriteAndDrain(t *testing.T) {
	rb := newRingBuffer(8)

	rb.Write(Event{Type: "call", Func: "a"})
	rb.Write(Event{Type: "ret", Func: "a"})

	events := rb.Drain()
	if len(events) != 2 {
		t.Fatalf("expected 2, got %d", len(events))
	}
	if events[0].Func != "a" || events[1].Type != "ret" {
		t.Error("unexpected event content")
	}

	// After drain, buffer should be empty
	events = rb.Drain()
	if len(events) != 0 {
		t.Fatalf("expected 0 after drain, got %d", len(events))
	}
}

func TestRingBufferOverflow(t *testing.T) {
	rb := newRingBuffer(4)

	for i := 0; i < 6; i++ {
		rb.Write(Event{Type: "call", Func: "x"})
	}

	events := rb.Drain()
	// Should have the last 4 events (oldest dropped)
	if len(events) != 4 {
		t.Fatalf("expected 4, got %d", len(events))
	}
}

func TestRingBufferConcurrentWrites(t *testing.T) {
	rb := newRingBuffer(1024)
	const writers = 10
	const eventsPerWriter = 100

	var wg sync.WaitGroup
	wg.Add(writers)
	for w := 0; w < writers; w++ {
		go func(id int) {
			defer wg.Done()
			for i := 0; i < eventsPerWriter; i++ {
				rb.Write(Event{Type: "call", Func: fmt.Sprintf("g%d.fn%d", id, i)})
			}
		}(w)
	}
	wg.Wait()

	events := rb.Drain()
	if len(events) != writers*eventsPerWriter {
		t.Fatalf("expected %d events, got %d", writers*eventsPerWriter, len(events))
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/tracert && go test -v -run TestRingBuffer`
Expected: FAIL — newRingBuffer not defined

- [ ] **Step 3: Implement ring buffer**

```
services/tracert/buffer.go
```
```go
package tracert

import "sync"

// ringBuffer is a fixed-size circular buffer for trace events.
// When full, oldest events are dropped.
type ringBuffer struct {
	mu    sync.Mutex
	buf   []Event
	size  int
	head  int // next write position
	count int
}

func newRingBuffer(size int) *ringBuffer {
	return &ringBuffer{
		buf:  make([]Event, size),
		size: size,
	}
}

func (rb *ringBuffer) Write(e Event) {
	rb.mu.Lock()
	rb.buf[rb.head] = e
	rb.head = (rb.head + 1) % rb.size
	if rb.count < rb.size {
		rb.count++
	}
	rb.mu.Unlock()
}

// Drain returns all buffered events in order and resets the buffer.
func (rb *ringBuffer) Drain() []Event {
	rb.mu.Lock()
	defer rb.mu.Unlock()

	if rb.count == 0 {
		return nil
	}

	events := make([]Event, rb.count)
	start := (rb.head - rb.count + rb.size) % rb.size
	for i := 0; i < rb.count; i++ {
		events[i] = rb.buf[(start+i)%rb.size]
	}
	rb.count = 0
	rb.head = 0
	return events
}
```

- [ ] **Step 4: Run buffer tests**

Run: `cd services/tracert && go test -v -run TestRingBuffer`
Expected: PASS

- [ ] **Step 5: Write failing test for Unix socket emitter**

```
services/tracert/emitter_test.go
```
```go
package tracert

import (
	"bufio"
	"encoding/json"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestEmitterWritesJSONLinesToSocket(t *testing.T) {
	dir := t.TempDir()
	socketPath := filepath.Join(dir, "trace.sock")

	// Start a listener
	ln, err := net.Listen("unix", socketPath)
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()

	received := make(chan Event, 10)
	go func() {
		conn, err := ln.Accept()
		if err != nil {
			return
		}
		defer conn.Close()
		scanner := bufio.NewScanner(conn)
		for scanner.Scan() {
			var e Event
			if err := json.Unmarshal(scanner.Bytes(), &e); err == nil {
				received <- e
			}
		}
	}()

	// Give listener time to start
	time.Sleep(10 * time.Millisecond)

	initEmitter(socketPath)
	emitEvent(Event{Type: "call", Func: "test.Fn", GoroutineID: 1, SpanID: 1})
	flushEmitter()

	select {
	case e := <-received:
		if e.Type != "call" || e.Func != "test.Fn" {
			t.Errorf("unexpected event: %+v", e)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("timeout waiting for event")
	}
}

func TestEmitterGracefulWhenNoSocket(t *testing.T) {
	// Should not panic when socket doesn't exist
	initEmitter("/nonexistent/path/trace.sock")
	emitEvent(Event{Type: "call", Func: "test.Fn"})
	flushEmitter()
	// No panic = pass
}

```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd services/tracert && go test -v -run TestEmitter`
Expected: FAIL — initEmitter not defined

- [ ] **Step 7: Implement emitter**

```
services/tracert/emitter.go
```
```go
package tracert

import (
	"bufio"
	"encoding/json"
	"log"
	"net"
	"sync"
)

var (
	emitterConn   net.Conn
	emitterWriter *bufio.Writer
	emitterMu     sync.Mutex
	emitterBuf    *ringBuffer
)

// initEmitter connects to the Unix socket for event emission.
func initEmitter(socketPath string) {
	emitterBuf = newRingBuffer(65536) // 64K event ring buffer

	conn, err := net.Dial("unix", socketPath)
	if err != nil {
		log.Printf("tracert: failed to connect to %s: %v (events will be buffered)", socketPath, err)
		return
	}
	emitterConn = conn
	emitterWriter = bufio.NewWriterSize(conn, 64*1024)
}

// emitEvent buffers an event and flushes when the buffer threshold is reached.
func emitEvent(e Event) {
	emitterBuf.Write(e)
}

// flushEmitter writes all buffered events to the socket.
func flushEmitter() {
	events := emitterBuf.Drain()
	if len(events) == 0 || emitterConn == nil {
		return
	}

	emitterMu.Lock()
	defer emitterMu.Unlock()

	enc := json.NewEncoder(emitterWriter)
	for _, e := range events {
		if err := enc.Encode(e); err != nil {
			log.Printf("tracert: encode error: %v", err)
		}
	}
	if err := emitterWriter.Flush(); err != nil {
		log.Printf("tracert: flush error: %v", err)
	}
}
```

- [ ] **Step 8: Run all tracert tests**

Run: `cd services/tracert && go test -v ./...`
Expected: All tests pass

- [ ] **Step 9: Commit**

```bash
git add services/tracert/
git commit -m "feat(tracert): ring buffer and Unix socket event emitter"
```

---

## Task 3: Go Instrumenter — Session Management and Scaffold

**Files:**
- Create: `services/go-instrumenter/go.mod`
- Create: `services/go-instrumenter/cmd/main.go`
- Create: `services/go-instrumenter/internal/handlers/health.go`
- Create: `services/go-instrumenter/internal/session/session.go`
- Create: `services/go-instrumenter/internal/session/session_test.go`
- Create: `services/go-instrumenter/Dockerfile`

- [ ] **Step 1: Create go.mod**

```
services/go-instrumenter/go.mod
```
```
module github.com/tersecontext/tc/services/go-instrumenter

go 1.24.0

require golang.org/x/tools v0.29.0
```

- [ ] **Step 2: Write failing test for session management**

```
services/go-instrumenter/internal/session/session_test.go
```
```go
package session

import (
	"os"
	"path/filepath"
	"testing"
)

func TestCreateSession(t *testing.T) {
	baseDir := t.TempDir()
	mgr := NewManager(baseDir)

	sess, err := mgr.Create()
	if err != nil {
		t.Fatal(err)
	}
	if sess.ID == "" {
		t.Error("session ID should not be empty")
	}
	if _, err := os.Stat(sess.Dir); os.IsNotExist(err) {
		t.Error("session directory should exist")
	}
}

func TestCleanupRespectsLockFiles(t *testing.T) {
	baseDir := t.TempDir()
	mgr := NewManager(baseDir)

	sess, _ := mgr.Create()

	// Create a lock file
	lockPath := filepath.Join(sess.Dir, ".lock")
	os.WriteFile(lockPath, []byte("locked"), 0644)

	// Cleanup should skip locked sessions
	mgr.CleanupExpired(0) // 0 = cleanup everything expired

	if _, err := os.Stat(sess.Dir); os.IsNotExist(err) {
		t.Error("locked session should not be cleaned up")
	}

	// Remove lock, cleanup should now work
	os.Remove(lockPath)
	mgr.CleanupExpired(0)

	if _, err := os.Stat(sess.Dir); !os.IsNotExist(err) {
		t.Error("unlocked session should be cleaned up")
	}
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services/go-instrumenter && go test -v ./internal/session/`
Expected: FAIL — package not found

- [ ] **Step 4: Implement session manager**

```
services/go-instrumenter/internal/session/session.go
```
```go
package session

import (
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/google/uuid"
)

// Session represents an instrumentation session with a temp directory.
type Session struct {
	ID        string
	Dir       string
	CreatedAt time.Time
}

// Manager handles session lifecycle.
type Manager struct {
	baseDir string
}

func NewManager(baseDir string) *Manager {
	os.MkdirAll(baseDir, 0755)
	return &Manager{baseDir: baseDir}
}

// Create initializes a new session with its own directory.
func (m *Manager) Create() (*Session, error) {
	id := uuid.New().String()
	dir := filepath.Join(m.baseDir, id)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return nil, fmt.Errorf("create session dir: %w", err)
	}
	return &Session{
		ID:        id,
		Dir:       dir,
		CreatedAt: time.Now(),
	}, nil
}

// CleanupExpired removes sessions older than maxAge that have no .lock file.
func (m *Manager) CleanupExpired(maxAge time.Duration) {
	entries, err := os.ReadDir(m.baseDir)
	if err != nil {
		return
	}
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		dir := filepath.Join(m.baseDir, entry.Name())

		// Skip locked sessions
		if _, err := os.Stat(filepath.Join(dir, ".lock")); err == nil {
			continue
		}

		info, err := entry.Info()
		if err != nil {
			continue
		}
		if maxAge > 0 && time.Since(info.ModTime()) < maxAge {
			continue
		}
		os.RemoveAll(dir)
	}
}
```

- [ ] **Step 5: Implement health handlers**

```
services/go-instrumenter/internal/handlers/health.go
```
```go
package handlers

import (
	"encoding/json"
	"net/http"
	"sync/atomic"
)

func Health(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"status":  "ok",
		"service": "go-instrumenter",
		"version": "0.1.0",
	})
}

func Ready(ready *atomic.Bool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if ready.Load() {
			json.NewEncoder(w).Encode(map[string]string{"status": "ready"})
		} else {
			w.WriteHeader(http.StatusServiceUnavailable)
			json.NewEncoder(w).Encode(map[string]string{"status": "not ready"})
		}
	}
}

func Metrics(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
}
```

- [ ] **Step 6: Implement cmd/main.go**

```
services/go-instrumenter/cmd/main.go
```
```go
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
	mux.HandleFunc("POST /instrument", handlers.Instrument(sessMgr))

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
```

- [ ] **Step 7: Create Dockerfile**

```
services/go-instrumenter/Dockerfile
```
```dockerfile
FROM golang:1.24-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o go-instrumenter ./cmd/main.go

FROM golang:1.24-alpine
# Runtime needs Go toolchain for building instrumented binaries
RUN apk add --no-cache ca-certificates git
COPY --from=builder /app/go-instrumenter /usr/local/bin/go-instrumenter
EXPOSE 8098
CMD ["go-instrumenter"]
```

- [ ] **Step 8: Run tests and tidy**

Run: `cd services/go-instrumenter && go mod tidy && go test -v ./...`
Expected: Session tests pass

- [ ] **Step 9: Commit**

```bash
git add services/go-instrumenter/
git commit -m "feat(go-instrumenter): scaffold with session management and health endpoints"
```

---

## Task 4: Go Instrumenter — AST Rewriter

**Files:**
- Create: `services/go-instrumenter/internal/rewriter/rewriter.go`
- Create: `services/go-instrumenter/internal/rewriter/patterns.go`
- Create: `services/go-instrumenter/internal/rewriter/rewriter_test.go`
- Create: `services/go-instrumenter/internal/rewriter/patterns_test.go`

- [ ] **Step 1: Write failing test for pattern matching**

```
services/go-instrumenter/internal/rewriter/patterns_test.go
```
```go
package rewriter

import "testing"

func TestBoundaryPatternMatching(t *testing.T) {
	patterns := []string{"*.Handler*", "db.*", "http.Client.*"}
	m := NewPatternMatcher(patterns)

	tests := []struct {
		funcName string
		want     bool
	}{
		{"OrderHandler.Create", true},
		{"UserHandler.Get", true},
		{"db.Query", true},
		{"db.Exec", true},
		{"http.Client.Do", true},
		{"processOrder", false},
		{"utils.Hash", false},
	}

	for _, tt := range tests {
		got := m.IsBoundary(tt.funcName)
		if got != tt.want {
			t.Errorf("IsBoundary(%q) = %v, want %v", tt.funcName, got, tt.want)
		}
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/go-instrumenter && go test -v ./internal/rewriter/ -run TestBoundary`
Expected: FAIL

- [ ] **Step 3: Implement pattern matcher**

```
services/go-instrumenter/internal/rewriter/patterns.go
```
```go
package rewriter

import (
	"path/filepath"
)

// PatternMatcher matches function names against glob patterns for boundary detection.
type PatternMatcher struct {
	patterns []string
}

func NewPatternMatcher(patterns []string) *PatternMatcher {
	return &PatternMatcher{patterns: patterns}
}

// IsBoundary returns true if the function name matches any boundary pattern.
// Supports dotted names: "db.Query" matches "db.*", "OrderHandler.Create" matches "*.Create".
func (m *PatternMatcher) IsBoundary(funcName string) bool {
	for _, p := range m.patterns {
		matched, _ := filepath.Match(p, funcName)
		if matched {
			return true
		}
	}
	return false
}
```

- [ ] **Step 4: Run pattern tests**

Run: `cd services/go-instrumenter && go test -v ./internal/rewriter/ -run TestBoundary`
Expected: PASS

- [ ] **Step 5: Write failing test for AST rewriting**

```
services/go-instrumenter/internal/rewriter/rewriter_test.go
```
```go
package rewriter

import (
	"strings"
	"testing"
)

func TestRewriteInteriorFunction(t *testing.T) {
	src := `package main

func ProcessOrder(ctx context.Context, order Order) error {
	return nil
}
`
	r := New("myrepo", NewPatternMatcher(nil))
	result, err := r.RewriteSource("orders.go", []byte(src))
	if err != nil {
		t.Fatal(err)
	}

	out := string(result)
	if !strings.Contains(out, `tracert.Enter("myrepo/main.ProcessOrder")`) {
		t.Error("should inject Enter call")
	}
	if !strings.Contains(out, "tracert.Exit(") {
		t.Error("should inject defer Exit call")
	}
	if !strings.Contains(out, `"github.com/tersecontext/tc/services/tracert"`) {
		t.Error("should add tracert import")
	}
}

func TestRewriteBoundaryFunction(t *testing.T) {
	src := `package handlers

func (h *OrderHandler) Create(w http.ResponseWriter, r *http.Request) {
	return
}
`
	patterns := []string{"*.Create"}
	r := New("myrepo", NewPatternMatcher(patterns))
	result, err := r.RewriteSource("handlers.go", []byte(src))
	if err != nil {
		t.Fatal(err)
	}

	out := string(result)
	if !strings.Contains(out, `tracert.Enter("myrepo/handlers.OrderHandler.Create", w, r)`) {
		t.Errorf("should inject Enter with args for boundary func, got:\n%s", out)
	}
}

func TestRewriteGoStatement(t *testing.T) {
	src := `package main

func Start() {
	go processAsync(item)
}
`
	r := New("myrepo", NewPatternMatcher(nil))
	result, err := r.RewriteSource("start.go", []byte(src))
	if err != nil {
		t.Fatal(err)
	}

	out := string(result)
	if !strings.Contains(out, "tracert.Go(func()") {
		t.Errorf("should rewrite go statement, got:\n%s", out)
	}
	// Verify the `go` keyword is removed — should be a plain call, not `go tracert.Go(...)`
	if strings.Contains(out, "go tracert.Go(") {
		t.Errorf("should NOT have 'go' keyword before tracert.Go — tracert.Go spawns internally, got:\n%s", out)
	}
}

func TestRewriteAnonymousGoroutine(t *testing.T) {
	src := `package main

func Start() {
	go func() {
		doWork()
	}()
}
`
	r := New("myrepo", NewPatternMatcher(nil))
	result, err := r.RewriteSource("start.go", []byte(src))
	if err != nil {
		t.Fatal(err)
	}

	out := string(result)
	if !strings.Contains(out, "tracert.Go(func()") {
		t.Errorf("should rewrite anonymous goroutine, got:\n%s", out)
	}
	if strings.Contains(out, "go tracert.Go(") {
		t.Errorf("should NOT have 'go' keyword before tracert.Go, got:\n%s", out)
	}
}

func TestSkipGeneratedFiles(t *testing.T) {
	src := `// Code generated by protoc. DO NOT EDIT.
package pb

func GeneratedFunc() {}
`
	r := New("myrepo", NewPatternMatcher(nil))
	result, err := r.RewriteSource("gen.go", []byte(src))
	if err != nil {
		t.Fatal(err)
	}

	out := string(result)
	if strings.Contains(out, "tracert.Enter") {
		t.Error("should not instrument generated files")
	}
}

func TestRewriteMethodWithReceiver(t *testing.T) {
	src := `package auth

type Service struct{}

func (s *Service) Authenticate(token string) (User, error) {
	return User{}, nil
}
`
	r := New("myrepo", NewPatternMatcher(nil))
	result, err := r.RewriteSource("auth.go", []byte(src))
	if err != nil {
		t.Fatal(err)
	}

	out := string(result)
	if !strings.Contains(out, `tracert.Enter("myrepo/auth.Service.Authenticate")`) {
		t.Errorf("should use Receiver.Method format, got:\n%s", out)
	}
}
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd services/go-instrumenter && go test -v ./internal/rewriter/ -run TestRewrite`
Expected: FAIL — New not defined

- [ ] **Step 7: Implement AST rewriter**

```
services/go-instrumenter/internal/rewriter/rewriter.go
```
```go
package rewriter

import (
	"bytes"
	"fmt"
	"go/ast"
	"go/parser"
	"go/printer"
	"go/token"

	"golang.org/x/tools/go/ast/astutil"
)

const tracertImport = `"github.com/tersecontext/tc/services/tracert"`

// Rewriter instruments Go source files with tracing calls.
type Rewriter struct {
	repo     string
	patterns *PatternMatcher
}

func New(repo string, patterns *PatternMatcher) *Rewriter {
	return &Rewriter{repo: repo, patterns: patterns}
}

// RewriteSource parses a Go source file and injects tracing hooks.
// Returns the rewritten source or the original if the file should be skipped.
func (rw *Rewriter) RewriteSource(filename string, src []byte) ([]byte, error) {
	// Skip generated files
	if isGenerated(src) {
		return src, nil
	}

	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, filename, src, parser.ParseComments)
	if err != nil {
		return nil, fmt.Errorf("parse %s: %w", filename, err)
	}

	modified := false
	pkgName := file.Name.Name

	// First pass: instrument function declarations (Enter/Exit injection)
	ast.Inspect(file, func(n ast.Node) bool {
		fn, ok := n.(*ast.FuncDecl)
		if !ok || fn.Body == nil {
			return true
		}
		funcID := rw.funcID(pkgName, fn)
		rw.instrumentFunc(fn, funcID)
		modified = true
		return true
	})

	// Second pass: replace GoStmt nodes using astutil.Apply
	// (astutil.Apply supports node replacement, unlike ast.Inspect)
	result := astutil.Apply(file, func(c *astutil.Cursor) bool {
		goStmt, ok := c.Node().(*ast.GoStmt)
		if !ok {
			return true
		}
		// Replace `go f()` with `tracert.Go(func() { f() })` (an ExprStmt, not a GoStmt)
		replacement := rw.makeGoReplacement(goStmt)
		c.Replace(replacement)
		modified = true
		return true
	}, nil)

	if !modified {
		return src, nil
	}

	resultFile := result.(*ast.File)

	// Add tracert import
	addImport(resultFile, tracertImport)

	var buf bytes.Buffer
	if err := printer.Fprint(&buf, fset, resultFile); err != nil {
		return nil, fmt.Errorf("print %s: %w", filename, err)
	}
	return buf.Bytes(), nil
}

// funcID returns the qualified function name: "repo/pkg.Receiver.Method" or "repo/pkg.Func"
func (rw *Rewriter) funcID(pkgName string, fn *ast.FuncDecl) string {
	name := fn.Name.Name
	if fn.Recv != nil && len(fn.Recv.List) > 0 {
		recvType := receiverTypeName(fn.Recv.List[0].Type)
		name = recvType + "." + name
	}
	return fmt.Sprintf("%s/%s.%s", rw.repo, pkgName, name)
}

// shortName extracts the Receiver.Method or FuncName portion from a fully qualified funcID.
func shortName(funcID string) string {
	// "repo/pkg.Receiver.Method" → find the last "/" then split on first "."
	slashIdx := -1
	for i := len(funcID) - 1; i >= 0; i-- {
		if funcID[i] == '/' {
			slashIdx = i
			break
		}
	}
	after := funcID
	if slashIdx >= 0 {
		after = funcID[slashIdx+1:]
	}
	// "pkg.Receiver.Method" → "Receiver.Method" (skip pkg prefix)
	dotIdx := -1
	for i, c := range after {
		if c == '.' {
			dotIdx = i
			break
		}
	}
	if dotIdx >= 0 {
		return after[dotIdx+1:]
	}
	return after
}

// instrumentFunc injects Enter/Exit calls at the start of a function body.
func (rw *Rewriter) instrumentFunc(fn *ast.FuncDecl, funcID string) {
	isBoundary := rw.patterns != nil && rw.patterns.IsBoundary(shortName(funcID))

	// Create: __span := tracert.Enter("funcID", [args...])
	enterArgs := []ast.Expr{&ast.BasicLit{Kind: token.STRING, Value: fmt.Sprintf("%q", funcID)}}
	if isBoundary && fn.Type.Params != nil {
		for _, field := range fn.Type.Params.List {
			for _, name := range field.Names {
				enterArgs = append(enterArgs, ast.NewIdent(name.Name))
			}
		}
	}

	enterStmt := &ast.AssignStmt{
		Lhs: []ast.Expr{ast.NewIdent("__span")},
		Tok: token.DEFINE,
		Rhs: []ast.Expr{
			&ast.CallExpr{
				Fun: &ast.SelectorExpr{
					X:   ast.NewIdent("tracert"),
					Sel: ast.NewIdent("Enter"),
				},
				Args: enterArgs,
			},
		},
	}

	// Create: defer func() { tracert.Exit(__span) }()
	exitCall := &ast.CallExpr{
		Fun: &ast.SelectorExpr{
			X:   ast.NewIdent("tracert"),
			Sel: ast.NewIdent("Exit"),
		},
		Args: []ast.Expr{ast.NewIdent("__span")},
	}
	deferStmt := &ast.DeferStmt{
		Call: &ast.CallExpr{
			Fun: &ast.FuncLit{
				Type: &ast.FuncType{Params: &ast.FieldList{}},
				Body: &ast.BlockStmt{
					List: []ast.Stmt{&ast.ExprStmt{X: exitCall}},
				},
			},
		},
	}

	// Prepend to function body
	fn.Body.List = append([]ast.Stmt{enterStmt, deferStmt}, fn.Body.List...)
}

// makeGoReplacement creates an ExprStmt that replaces a GoStmt.
// `go f()` becomes `tracert.Go(func() { f() })` (no `go` keyword — tracert.Go spawns internally).
func (rw *Rewriter) makeGoReplacement(goStmt *ast.GoStmt) *ast.ExprStmt {
	originalCall := goStmt.Call

	var wrappedBody *ast.BlockStmt

	// Check if it's `go func() { ... }()` — anonymous goroutine
	if funcLit, ok := originalCall.Fun.(*ast.FuncLit); ok {
		wrappedBody = funcLit.Body
	} else {
		// Simple case: `go f(args...)` or `go obj.Method(args...)`
		wrappedBody = &ast.BlockStmt{
			List: []ast.Stmt{&ast.ExprStmt{X: originalCall}},
		}
	}

	return &ast.ExprStmt{
		X: &ast.CallExpr{
			Fun: &ast.SelectorExpr{
				X:   ast.NewIdent("tracert"),
				Sel: ast.NewIdent("Go"),
			},
			Args: []ast.Expr{
				&ast.FuncLit{
					Type: &ast.FuncType{Params: &ast.FieldList{}},
					Body: wrappedBody,
				},
			},
		},
	}
}

func receiverTypeName(expr ast.Expr) string {
	switch t := expr.(type) {
	case *ast.StarExpr:
		return receiverTypeName(t.X)
	case *ast.Ident:
		return t.Name
	default:
		return "Unknown"
	}
}

func isGenerated(src []byte) bool {
	lines := bytes.SplitN(src, []byte("\n"), 5)
	for _, line := range lines {
		if bytes.Contains(line, []byte("Code generated")) {
			return true
		}
	}
	return false
}

func addImport(file *ast.File, importPath string) {
	for _, imp := range file.Imports {
		if imp.Path.Value == importPath {
			return
		}
	}

	newImport := &ast.ImportSpec{
		Path: &ast.BasicLit{Kind: token.STRING, Value: importPath},
	}

	for _, decl := range file.Decls {
		if genDecl, ok := decl.(*ast.GenDecl); ok && genDecl.Tok == token.IMPORT {
			genDecl.Specs = append(genDecl.Specs, newImport)
			return
		}
	}

	file.Decls = append([]ast.Decl{
		&ast.GenDecl{
			Tok:   token.IMPORT,
			Specs: []ast.Spec{newImport},
		},
	}, file.Decls...)
}
```

- [ ] **Step 8: Run all rewriter tests**

Run: `cd services/go-instrumenter && go test -v ./internal/rewriter/`
Expected: All tests pass

- [ ] **Step 9: Commit**

```bash
git add services/go-instrumenter/internal/rewriter/
git commit -m "feat(go-instrumenter): AST rewriter with Enter/Exit injection and go statement rewriting"
```

---

## Task 5: Go Instrumenter — Builder and Coverage Pre-scan

**Files:**
- Create: `services/go-instrumenter/internal/builder/builder.go`
- Create: `services/go-instrumenter/internal/builder/builder_test.go`
- Create: `services/go-instrumenter/internal/coverage/coverage.go`
- Create: `services/go-instrumenter/internal/coverage/coverage_test.go`

- [ ] **Step 1: Write failing test for coverage profile parsing**

```
services/go-instrumenter/internal/coverage/coverage_test.go
```
```go
package coverage

import (
	"testing"
)

func TestParseCoverFunc(t *testing.T) {
	// Simulated output of `go tool cover -func=cover.out`
	output := `auth/service.go:12:	Authenticate		100.0%
auth/service.go:30:	ValidateToken		80.0%
handlers/order.go:15:	CreateOrder		0.0%
total:			(statements)		60.0%
`
	funcs := ParseCoverFunc(output)

	if len(funcs) != 3 {
		t.Fatalf("expected 3 functions, got %d", len(funcs))
	}

	// Authenticate should be covered
	if !funcs["auth/service.go:Authenticate"] {
		t.Error("Authenticate should be marked as covered")
	}
	// CreateOrder at 0.0% should not be covered
	if funcs["handlers/order.go:CreateOrder"] {
		t.Error("CreateOrder at 0.0% should not be covered")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/go-instrumenter && go test -v ./internal/coverage/ -run TestParse`
Expected: FAIL

- [ ] **Step 3: Implement coverage parser**

```
services/go-instrumenter/internal/coverage/coverage.go
```
```go
package coverage

import (
	"bufio"
	"fmt"
	"os/exec"
	"path/filepath"
	"strings"
)

// ParseCoverFunc parses `go tool cover -func` output.
// Returns a map of "file:funcName" → covered (true if coverage > 0%).
func ParseCoverFunc(output string) map[string]bool {
	result := make(map[string]bool)
	scanner := bufio.NewScanner(strings.NewReader(output))
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "total:") {
			continue
		}
		// Format: "file:line:\tFuncName\t\tCoverage%"
		parts := strings.Fields(line)
		if len(parts) < 3 {
			continue
		}
		fileLine := parts[0]
		funcName := parts[1]
		covStr := parts[len(parts)-1]

		// Extract file path (before the last colon+line)
		lastColon := strings.LastIndex(fileLine, ":")
		if lastColon < 0 {
			continue
		}
		file := fileLine[:lastColon]

		covered := covStr != "0.0%"
		key := file + ":" + funcName
		result[key] = covered
	}
	return result
}

// RunCoverProfile runs `go test -coverprofile` and returns covered functions.
func RunCoverProfile(repoPath string, testPattern string) (map[string]bool, error) {
	coverFile := filepath.Join(repoPath, "cover.out")

	args := []string{"test", "-coverprofile=" + coverFile, "-covermode=atomic"}
	if testPattern != "" {
		args = append(args, "-run="+testPattern)
	}
	args = append(args, "./...")

	cmd := exec.Command("go", args...)
	cmd.Dir = repoPath
	if out, err := cmd.CombinedOutput(); err != nil {
		return nil, fmt.Errorf("go test -coverprofile failed: %w\n%s", err, out)
	}

	// Parse the profile
	funcCmd := exec.Command("go", "tool", "cover", "-func="+coverFile)
	funcCmd.Dir = repoPath
	out, err := funcCmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("go tool cover -func failed: %w\n%s", err, out)
	}

	return ParseCoverFunc(string(out)), nil
}
```

- [ ] **Step 4: Run coverage parser tests**

Run: `cd services/go-instrumenter && go test -v ./internal/coverage/`
Expected: PASS

- [ ] **Step 5: Write failing test for builder**

```
services/go-instrumenter/internal/builder/builder_test.go
```
```go
package builder

import (
	"os"
	"path/filepath"
	"testing"
)

func TestModEditAddsTracertDependency(t *testing.T) {
	// Create a minimal Go module in temp
	dir := t.TempDir()
	gomod := `module example.com/test

go 1.24.0
`
	os.WriteFile(filepath.Join(dir, "go.mod"), []byte(gomod), 0644)
	os.WriteFile(filepath.Join(dir, "main.go"), []byte("package main\nfunc main(){}\n"), 0644)

	b := New("/fake/tracert/path")
	err := b.InjectTracertDep(dir)
	if err != nil {
		t.Fatal(err)
	}

	// Verify go.mod was modified
	content, _ := os.ReadFile(filepath.Join(dir, "go.mod"))
	if !contains(string(content), "tracert") {
		t.Error("go.mod should contain tracert dependency")
	}
}

func contains(s, substr string) bool {
	return len(s) > 0 && len(substr) > 0 && indexOf(s, substr) >= 0
}

func indexOf(s, substr string) int {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return i
		}
	}
	return -1
}
```

- [ ] **Step 6: Implement builder**

```
services/go-instrumenter/internal/builder/builder.go
```
```go
package builder

import (
	"fmt"
	"os/exec"
)

// Builder handles injecting tracert dependency and compiling instrumented code.
type Builder struct {
	tracertPath string // local path to tracert module
}

func New(tracertPath string) *Builder {
	return &Builder{tracertPath: tracertPath}
}

// InjectTracertDep adds the tracert module as a dependency using go mod edit.
func (b *Builder) InjectTracertDep(repoDir string) error {
	modPath := "github.com/tersecontext/tc/services/tracert"

	requireCmd := exec.Command("go", "mod", "edit",
		"-require="+modPath+"@v0.0.0")
	requireCmd.Dir = repoDir
	if out, err := requireCmd.CombinedOutput(); err != nil {
		return fmt.Errorf("go mod edit -require: %w\n%s", err, out)
	}

	replaceCmd := exec.Command("go", "mod", "edit",
		"-replace="+modPath+"="+b.tracertPath)
	replaceCmd.Dir = repoDir
	if out, err := replaceCmd.CombinedOutput(); err != nil {
		return fmt.Errorf("go mod edit -replace: %w\n%s", err, out)
	}

	return nil
}

// BuildBinary compiles the instrumented source into a binary.
func (b *Builder) BuildBinary(repoDir, outputPath, buildTarget string) error {
	cmd := exec.Command("go", "build", "-o", outputPath, buildTarget)
	cmd.Dir = repoDir
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("go build: %w\n%s", err, out)
	}
	return nil
}

// BuildTestBinary compiles an instrumented test binary.
func (b *Builder) BuildTestBinary(repoDir, outputPath, pkg string) error {
	cmd := exec.Command("go", "test", "-c", "-o", outputPath, pkg)
	cmd.Dir = repoDir
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("go test -c: %w\n%s", err, out)
	}
	return nil
}
```

- [ ] **Step 7: Run all go-instrumenter tests**

Run: `cd services/go-instrumenter && go mod tidy && go test -v ./...`
Expected: All tests pass

- [ ] **Step 8: Commit**

```bash
git add services/go-instrumenter/internal/builder/ services/go-instrumenter/internal/coverage/
git commit -m "feat(go-instrumenter): coverage pre-scan and build pipeline"
```

---

## Task 6: Go Instrumenter — POST /instrument Handler

**Files:**
- Create: `services/go-instrumenter/internal/handlers/instrument.go`
- Create: `services/go-instrumenter/internal/handlers/instrument_test.go`

- [ ] **Step 1: Write failing test for /instrument endpoint**

```
services/go-instrumenter/internal/handlers/instrument_test.go
```
```go
package handlers

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/tersecontext/tc/services/go-instrumenter/internal/session"
)

func TestInstrumentReturns200WithSessionID(t *testing.T) {
	// Create a minimal Go repo in a temp dir
	repoDir := t.TempDir()
	os.WriteFile(filepath.Join(repoDir, "go.mod"), []byte("module example.com/test\n\ngo 1.24.0\n"), 0644)
	os.WriteFile(filepath.Join(repoDir, "main.go"), []byte("package main\n\nfunc main() {}\n\nfunc TestLogin() {}\n"), 0644)

	sessMgr := session.NewManager(t.TempDir())
	handler := Instrument(sessMgr)

	body, _ := json.Marshal(map[string]any{
		"repo":               "test-repo",
		"repo_path":          repoDir,
		"commit_sha":         "abc123",
		"entrypoints":        []string{"TestLogin"},
		"language":           "go",
		"boundary_patterns":  []string{"*.Handler*"},
		"include_deps":       []string{},
	})

	req := httptest.NewRequest("POST", "/instrument", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	handler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]any
	json.Unmarshal(w.Body.Bytes(), &resp)
	if resp["session_id"] == nil || resp["session_id"] == "" {
		t.Error("response should contain session_id")
	}
}

func TestInstrumentRejects400OnMissingFields(t *testing.T) {
	sessMgr := session.NewManager(t.TempDir())
	handler := Instrument(sessMgr)

	body, _ := json.Marshal(map[string]any{"repo": "test"})
	req := httptest.NewRequest("POST", "/instrument", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	handler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/go-instrumenter && go test -v ./internal/handlers/ -run TestInstrument`
Expected: FAIL — Instrument not defined

- [ ] **Step 3: Implement /instrument handler**

```
services/go-instrumenter/internal/handlers/instrument.go
```
```go
package handlers

import (
	"encoding/json"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"

	"github.com/tersecontext/tc/services/go-instrumenter/internal/builder"
	"github.com/tersecontext/tc/services/go-instrumenter/internal/rewriter"
	"github.com/tersecontext/tc/services/go-instrumenter/internal/session"
)

type InstrumentRequest struct {
	Repo             string   `json:"repo"`
	RepoPath         string   `json:"repo_path"`
	CommitSha        string   `json:"commit_sha"`
	Entrypoints      []string `json:"entrypoints"`
	Language         string   `json:"language"`
	BoundaryPatterns []string `json:"boundary_patterns"`
	IncludeDeps      []string `json:"include_deps"`
}

type InstrumentResponse struct {
	SessionID         string `json:"session_id"`
	BinaryPath        string `json:"binary_path"`
	BinaryType        string `json:"binary_type"`
	InstrumentedFuncs int    `json:"instrumented_funcs"`
	SkippedFuncs      int    `json:"skipped_funcs"`
	CoverageFiltered  int    `json:"coverage_filtered"`
}

func Instrument(sessMgr *session.Manager) http.HandlerFunc {
	tracertPath := os.Getenv("TRACERT_PATH")
	if tracertPath == "" {
		tracertPath = "/app/tracert"
	}

	return func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			http.Error(w, "failed to read body", http.StatusBadRequest)
			return
		}

		var req InstrumentRequest
		if err := json.Unmarshal(body, &req); err != nil {
			http.Error(w, "invalid JSON: "+err.Error(), http.StatusBadRequest)
			return
		}

		if req.Repo == "" || req.RepoPath == "" || req.CommitSha == "" || len(req.Entrypoints) == 0 {
			http.Error(w, "missing required fields: repo, repo_path, commit_sha, entrypoints", http.StatusBadRequest)
			return
		}

		// Create session
		sess, err := sessMgr.Create()
		if err != nil {
			log.Printf("session create error: %v", err)
			http.Error(w, "internal error", http.StatusInternalServerError)
			return
		}

		// Copy source to session directory
		srcDir := filepath.Join(sess.Dir, "src")
		if err := copyDir(req.RepoPath, srcDir); err != nil {
			log.Printf("copy source error: %v", err)
			http.Error(w, "failed to copy source", http.StatusInternalServerError)
			return
		}

		// AST rewrite
		patterns := rewriter.NewPatternMatcher(req.BoundaryPatterns)
		rw := rewriter.New(req.Repo, patterns)
		stats, err := rw.RewriteDir(srcDir)
		if err != nil {
			log.Printf("rewrite error: %v", err)
			http.Error(w, "AST rewrite failed", http.StatusInternalServerError)
			return
		}

		// Inject tracert dependency and build
		b := builder.New(tracertPath)
		if err := b.InjectTracertDep(srcDir); err != nil {
			log.Printf("inject dep error: %v", err)
			http.Error(w, "failed to inject tracert dependency", http.StatusInternalServerError)
			return
		}

		binaryPath := filepath.Join(sess.Dir, "binary")
		binaryType := "test"

		// Determine if test or server binary
		isTest := false
		for _, ep := range req.Entrypoints {
			if len(ep) >= 4 && ep[:4] == "Test" {
				isTest = true
				break
			}
		}

		if isTest {
			if err := b.BuildTestBinary(srcDir, binaryPath, "./..."); err != nil {
				log.Printf("build error: %v", err)
				http.Error(w, "build failed", http.StatusInternalServerError)
				return
			}
		} else {
			binaryType = "server"
			if err := b.BuildBinary(srcDir, binaryPath, "./cmd/server"); err != nil {
				log.Printf("build error: %v", err)
				http.Error(w, "build failed", http.StatusInternalServerError)
				return
			}
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(InstrumentResponse{
			SessionID:         sess.ID,
			BinaryPath:        binaryPath,
			BinaryType:        binaryType,
			InstrumentedFuncs: stats.Instrumented,
			SkippedFuncs:      stats.Skipped,
			CoverageFiltered:  stats.CoverageFiltered,
		})
	}
}

// copyDir recursively copies a directory. Simplified — skips symlinks.
func copyDir(src, dst string) error {
	return filepath.Walk(src, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(src, path)
		target := filepath.Join(dst, rel)

		if info.IsDir() {
			return os.MkdirAll(target, info.Mode())
		}

		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(target, data, info.Mode())
	})
}
```

- [ ] **Step 4: Add RewriteDir method and stats to rewriter**

Add to `services/go-instrumenter/internal/rewriter/rewriter.go`:

```go
// RewriteStats tracks instrumentation metrics.
type RewriteStats struct {
	Instrumented     int
	Skipped          int
	CoverageFiltered int
}

// RewriteDir rewrites all .go files in a directory tree.
func (rw *Rewriter) RewriteDir(dir string) (RewriteStats, error) {
	var stats RewriteStats

	err := filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() || filepath.Ext(path) != ".go" {
			return nil
		}
		// Skip test files and CGo
		base := filepath.Base(path)
		if strings.HasSuffix(base, "_test.go") || strings.HasPrefix(base, "cgo_") {
			stats.Skipped++
			return nil
		}

		src, err := os.ReadFile(path)
		if err != nil {
			return err
		}

		result, err := rw.RewriteSource(path, src)
		if err != nil {
			stats.Skipped++
			return nil // skip files that fail to parse
		}

		if string(result) == string(src) {
			stats.Skipped++
			return nil
		}

		stats.Instrumented++
		return os.WriteFile(path, result, info.Mode())
	})

	return stats, err
}
```

Add necessary imports: `"os"` and `"path/filepath"` to the rewriter.go imports.

- [ ] **Step 5: Run handler tests**

Run: `cd services/go-instrumenter && go mod tidy && go test -v ./internal/handlers/`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/go-instrumenter/internal/handlers/
git commit -m "feat(go-instrumenter): POST /instrument endpoint with full rewrite+build pipeline"
```

---

## Task 7: Go Trace Runner — Collector and RawTrace Assembler

**Files:**
- Create: `services/go-trace-runner/go.mod`
- Create: `services/go-trace-runner/internal/collector/collector.go`
- Create: `services/go-trace-runner/internal/collector/collector_test.go`
- Create: `services/go-trace-runner/internal/assembler/assembler.go`
- Create: `services/go-trace-runner/internal/assembler/assembler_test.go`

- [ ] **Step 1: Create go.mod**

```
services/go-trace-runner/go.mod
```
```
module github.com/tersecontext/tc/services/go-trace-runner

go 1.24.0

require github.com/redis/go-redis/v9 v9.7.3
```

- [ ] **Step 2: Write failing test for collector event parsing and type mapping**

```
services/go-trace-runner/internal/collector/collector_test.go
```
```go
package collector

import (
	"testing"
)

func TestParseWireEvent(t *testing.T) {
	line := `{"t":"call","f":"auth.Login","file":"auth.go","l":10,"ts":0.5,"g":1,"s":1}`

	event, err := ParseWireEvent([]byte(line))
	if err != nil {
		t.Fatal(err)
	}
	if event.Type != "call" {
		t.Errorf("expected call, got %s", event.Type)
	}
	if event.Func != "auth.Login" {
		t.Errorf("expected auth.Login, got %s", event.Func)
	}
}

func TestMapWireTypeToTraceEvent(t *testing.T) {
	tests := []struct {
		wireType string
		want     string
	}{
		{"call", "call"},
		{"ret", "return"},
		{"panic", "exception"},
	}
	for _, tt := range tests {
		got := MapEventType(tt.wireType)
		if got != tt.want {
			t.Errorf("MapEventType(%q) = %q, want %q", tt.wireType, got, tt.want)
		}
	}
}

func TestGroupEventsByGoroutine(t *testing.T) {
	events := []WireEvent{
		{Type: "call", Func: "main.A", GoroutineID: 1, SpanID: 1},
		{Type: "call", Func: "main.B", GoroutineID: 2, SpanID: 2},
		{Type: "ret", Func: "", GoroutineID: 1, SpanID: 1},
		{Type: "ret", Func: "", GoroutineID: 2, SpanID: 2},
	}

	groups := GroupByGoroutine(events)
	if len(groups) != 2 {
		t.Fatalf("expected 2 goroutine groups, got %d", len(groups))
	}
	if len(groups[1]) != 2 || len(groups[2]) != 2 {
		t.Error("each goroutine should have 2 events")
	}
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services/go-trace-runner && go test -v ./internal/collector/ -run Test`
Expected: FAIL

- [ ] **Step 4: Implement collector**

```
services/go-trace-runner/internal/collector/collector.go
```
```go
package collector

import (
	"bufio"
	"encoding/json"
	"net"
)

// WireEvent is the raw event format from the tracert runtime.
type WireEvent struct {
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

// ParseWireEvent parses a JSON line into a WireEvent.
func ParseWireEvent(line []byte) (WireEvent, error) {
	var e WireEvent
	err := json.Unmarshal(line, &e)
	return e, err
}

// MapEventType converts wire format types to RawTrace TraceEvent types.
func MapEventType(wireType string) string {
	switch wireType {
	case "ret":
		return "return"
	case "panic":
		return "exception"
	default:
		return wireType // "call" stays "call"
	}
}

// GroupByGoroutine groups events by goroutine ID.
func GroupByGoroutine(events []WireEvent) map[uint64][]WireEvent {
	groups := make(map[uint64][]WireEvent)
	for _, e := range events {
		groups[e.GoroutineID] = append(groups[e.GoroutineID], e)
	}
	return groups
}

// ReadFromSocket reads all events from a Unix socket until it closes.
func ReadFromSocket(socketPath string) ([]WireEvent, error) {
	ln, err := net.Listen("unix", socketPath)
	if err != nil {
		return nil, err
	}
	defer ln.Close()

	conn, err := ln.Accept()
	if err != nil {
		return nil, err
	}
	defer conn.Close()

	var events []WireEvent
	scanner := bufio.NewScanner(conn)
	scanner.Buffer(make([]byte, 1024*1024), 1024*1024) // 1MB line buffer
	for scanner.Scan() {
		e, err := ParseWireEvent(scanner.Bytes())
		if err != nil {
			continue // skip malformed lines
		}
		events = append(events, e)
	}
	return events, scanner.Err()
}
```

- [ ] **Step 5: Run collector tests**

Run: `cd services/go-trace-runner && go test -v ./internal/collector/`
Expected: PASS

- [ ] **Step 6: Write failing test for RawTrace assembler**

```
services/go-trace-runner/internal/assembler/assembler_test.go
```
```go
package assembler

import (
	"testing"

	"github.com/tersecontext/tc/services/go-trace-runner/internal/collector"
)

func TestAssembleRawTrace(t *testing.T) {
	events := []collector.WireEvent{
		{Type: "call", Func: "auth.Login", File: "auth.go", Line: 10, TimestampMs: 0, GoroutineID: 1, SpanID: 1},
		{Type: "call", Func: "db.Query", File: "auth.go", Line: 20, TimestampMs: 5, GoroutineID: 1, SpanID: 2},
		{Type: "ret", Func: "", File: "auth.go", Line: 20, TimestampMs: 18, GoroutineID: 1, SpanID: 2},
		{Type: "ret", Func: "", File: "auth.go", Line: 10, TimestampMs: 25, GoroutineID: 1, SpanID: 1},
	}

	trace := Assemble("sha256:abc123", "def456", "myrepo", events)

	if trace.EntrypointStableID != "sha256:abc123" {
		t.Errorf("wrong stable_id: %s", trace.EntrypointStableID)
	}
	if trace.CommitSha != "def456" {
		t.Errorf("wrong commit_sha: %s", trace.CommitSha)
	}
	if len(trace.Events) != 4 {
		t.Fatalf("expected 4 events, got %d", len(trace.Events))
	}

	// Check type mapping: "ret" should become "return"
	if trace.Events[2].Type != "return" {
		t.Errorf("expected 'return', got %s", trace.Events[2].Type)
	}
	if trace.Events[0].Type != "call" {
		t.Errorf("expected 'call', got %s", trace.Events[0].Type)
	}

	// Duration should be max timestamp
	if trace.DurationMs != 25 {
		t.Errorf("expected duration 25, got %f", trace.DurationMs)
	}
}

func TestAssemblePanicEvent(t *testing.T) {
	events := []collector.WireEvent{
		{Type: "call", Func: "handler.Create", File: "h.go", Line: 5, TimestampMs: 0, GoroutineID: 1, SpanID: 1},
		{Type: "panic", Func: "", File: "h.go", Line: 12, TimestampMs: 10, GoroutineID: 1, SpanID: 1, ExcType: "nil pointer"},
	}

	trace := Assemble("sha256:xyz", "abc", "repo", events)

	if len(trace.Events) != 2 {
		t.Fatalf("expected 2 events, got %d", len(trace.Events))
	}
	if trace.Events[1].Type != "exception" {
		t.Errorf("expected 'exception', got %s", trace.Events[1].Type)
	}
	if trace.Events[1].ExcType != "nil pointer" {
		t.Errorf("expected 'nil pointer', got %s", trace.Events[1].ExcType)
	}
}
```

- [ ] **Step 7: Implement assembler**

```
services/go-trace-runner/internal/assembler/assembler.go
```
```go
package assembler

import (
	"github.com/tersecontext/tc/services/go-trace-runner/internal/collector"
)

// TraceEvent matches the Python TraceEvent Pydantic model schema.
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

// RawTrace matches the Python RawTrace Pydantic model schema.
type RawTrace struct {
	EntrypointStableID string       `json:"entrypoint_stable_id"`
	CommitSha          string       `json:"commit_sha"`
	Repo               string       `json:"repo"`
	DurationMs         float64      `json:"duration_ms"`
	Events             []TraceEvent `json:"events"`
}

// Assemble converts wire events into a RawTrace.
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
```

- [ ] **Step 8: Run all go-trace-runner tests**

Run: `cd services/go-trace-runner && go mod tidy && go test -v ./...`
Expected: All tests pass

- [ ] **Step 9: Commit**

```bash
git add services/go-trace-runner/
git commit -m "feat(go-trace-runner): collector with type mapping and RawTrace assembler"
```

---

## Task 8: Go Trace Runner — Executor and HTTP API

**Files:**
- Create: `services/go-trace-runner/internal/executor/executor.go`
- Create: `services/go-trace-runner/internal/executor/executor_test.go`
- Create: `services/go-trace-runner/internal/handlers/health.go`
- Create: `services/go-trace-runner/internal/handlers/run.go`
- Create: `services/go-trace-runner/cmd/main.go`
- Create: `services/go-trace-runner/Dockerfile`

- [ ] **Step 1: Write failing test for executor**

```
services/go-trace-runner/internal/executor/executor_test.go
```
```go
package executor

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLockFileCreatedDuringExecution(t *testing.T) {
	sessionDir := t.TempDir()

	exec := New(sessionDir)
	lockPath := filepath.Join(sessionDir, ".lock")

	exec.CreateLock()
	if _, err := os.Stat(lockPath); os.IsNotExist(err) {
		t.Error("lock file should exist during execution")
	}

	exec.ReleaseLock()
	if _, err := os.Stat(lockPath); !os.IsNotExist(err) {
		t.Error("lock file should be removed after execution")
	}
}

func TestTimeoutCalculation(t *testing.T) {
	exec := New(t.TempDir())

	timeout := exec.Timeout("test", 30)
	if timeout.Seconds() != 30 {
		t.Errorf("test timeout should be 30s, got %v", timeout)
	}

	timeout = exec.Timeout("server", 30)
	if timeout.Seconds() != 60 {
		t.Errorf("server timeout should be 60s (doubled), got %v", timeout)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/go-trace-runner && go test -v ./internal/executor/ -run Test`
Expected: FAIL

- [ ] **Step 3: Implement executor**

```
services/go-trace-runner/internal/executor/executor.go
```
```go
package executor

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

// Executor manages running an instrumented binary and collecting traces.
type Executor struct {
	sessionDir string
	lockPath   string
}

func New(sessionDir string) *Executor {
	return &Executor{
		sessionDir: sessionDir,
		lockPath:   filepath.Join(sessionDir, ".lock"),
	}
}

func (e *Executor) CreateLock() error {
	return os.WriteFile(e.lockPath, []byte(fmt.Sprintf("%d", os.Getpid())), 0644)
}

func (e *Executor) ReleaseLock() {
	os.Remove(e.lockPath)
}

// Timeout returns the appropriate timeout for the given binary type.
func (e *Executor) Timeout(binaryType string, requestedSeconds int) time.Duration {
	if binaryType == "server" {
		return time.Duration(requestedSeconds*2) * time.Second
	}
	return time.Duration(requestedSeconds) * time.Second
}

// RunTestBinary executes a test binary with the given test pattern and timeout.
func (e *Executor) RunTestBinary(ctx context.Context, binaryPath, testPattern string, timeout time.Duration, socketPath string) error {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	args := []string{"-test.timeout=" + timeout.String()}
	if testPattern != "" {
		args = append(args, "-test.run="+testPattern)
	}

	cmd := exec.CommandContext(ctx, binaryPath, args...)
	cmd.Env = append(os.Environ(), "TRACERT_SOCKET="+socketPath)
	cmd.Dir = e.sessionDir

	output, err := cmd.CombinedOutput()
	if ctx.Err() == context.DeadlineExceeded {
		return fmt.Errorf("test execution timed out after %v", timeout)
	}
	if err != nil {
		return fmt.Errorf("test binary failed: %w\n%s", err, output)
	}
	return nil
}
```

- [ ] **Step 4: Run executor tests**

Run: `cd services/go-trace-runner && go test -v ./internal/executor/`
Expected: PASS

- [ ] **Step 5: Create health handlers (same pattern as go-instrumenter)**

```
services/go-trace-runner/internal/handlers/health.go
```
```go
package handlers

import (
	"encoding/json"
	"net/http"
	"sync/atomic"
)

func Health(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"status":  "ok",
		"service": "go-trace-runner",
		"version": "0.1.0",
	})
}

func Ready(ready *atomic.Bool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if ready.Load() {
			json.NewEncoder(w).Encode(map[string]string{"status": "ready"})
		} else {
			w.WriteHeader(http.StatusServiceUnavailable)
			json.NewEncoder(w).Encode(map[string]string{"status": "not ready"})
		}
	}
}

func Metrics(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
}
```

- [ ] **Step 6: Create run handler and main.go**

```
services/go-trace-runner/internal/handlers/run.go
```
```go
package handlers

import (
	"encoding/json"
	"io"
	"net/http"
	"sync"

	"github.com/google/uuid"
)

type RunRequest struct {
	SessionID   string        `json:"session_id"`
	Entrypoints []Entrypoint  `json:"entrypoints"`
	CommitSha   string        `json:"commit_sha"`
	Repo        string        `json:"repo"`
	TimeoutS    int           `json:"timeout_s"`
}

type Entrypoint struct {
	StableID string `json:"stable_id"`
	Name     string `json:"name"`
	Type     string `json:"type"` // "test" or "server"
}

type RunStatus struct {
	TraceID       string `json:"trace_id"`
	Status        string `json:"status"` // "running", "completed", "failed"
	EventsEmitted int    `json:"events_emitted,omitempty"`
	DurationMs    int64  `json:"duration_ms,omitempty"`
	Error         string `json:"error,omitempty"`
}

var (
	runStatuses sync.Map // traceID → *RunStatus
)

func Run(sessionsDir string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			http.Error(w, "failed to read body", http.StatusBadRequest)
			return
		}

		var req RunRequest
		if err := json.Unmarshal(body, &req); err != nil {
			http.Error(w, "invalid JSON", http.StatusBadRequest)
			return
		}

		if req.SessionID == "" || len(req.Entrypoints) == 0 {
			http.Error(w, "missing session_id or entrypoints", http.StatusBadRequest)
			return
		}
		if req.TimeoutS <= 0 {
			req.TimeoutS = 30
		}

		traceID := uuid.New().String()
		status := &RunStatus{TraceID: traceID, Status: "running"}
		runStatuses.Store(traceID, status)

		// TODO: Task 9 implements the actual execution + Redis emission
		// For now, accept the request and return the trace ID

		w.WriteHeader(http.StatusAccepted)
		json.NewEncoder(w).Encode(status)
	}
}

func RunStatusHandler(w http.ResponseWriter, r *http.Request) {
	traceID := r.PathValue("id")
	if traceID == "" {
		http.Error(w, "missing trace id", http.StatusBadRequest)
		return
	}

	val, ok := runStatuses.Load(traceID)
	if !ok {
		http.Error(w, "trace not found", http.StatusNotFound)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(val)
}
```

```
services/go-trace-runner/cmd/main.go
```
```go
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

	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", handlers.Health)
	mux.HandleFunc("GET /ready", handlers.Ready(&ready))
	mux.HandleFunc("GET /metrics", handlers.Metrics)
	mux.HandleFunc("POST /run", handlers.Run(sessionsDir))
	mux.HandleFunc("GET /run/{id}/status", handlers.RunStatusHandler)

	srv := &http.Server{Addr: ":" + port, Handler: mux}

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
```

- [ ] **Step 7: Create Dockerfile**

```
services/go-trace-runner/Dockerfile
```
```dockerfile
FROM golang:1.24-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o go-trace-runner ./cmd/main.go

FROM alpine:3.19
RUN apk add --no-cache ca-certificates
COPY --from=builder /app/go-trace-runner /usr/local/bin/go-trace-runner
EXPOSE 8099
CMD ["go-trace-runner"]
```

- [ ] **Step 8: Run all go-trace-runner tests**

Run: `cd services/go-trace-runner && go mod tidy && go test -v ./...`
Expected: All tests pass

- [ ] **Step 9: Commit**

```bash
git add services/go-trace-runner/
git commit -m "feat(go-trace-runner): executor, HTTP API, and Dockerfile"
```

---

## Task 9: Go Trace Runner — Redis Integration (BLPOP, XADD, Cache)

**Files:**
- Create: `services/go-trace-runner/internal/stream/stream.go`
- Create: `services/go-trace-runner/internal/stream/stream_test.go`
- Modify: `services/go-trace-runner/cmd/main.go`

- [ ] **Step 1: Write failing test for Redis stream emission and cache**

```
services/go-trace-runner/internal/stream/stream_test.go
```
```go
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
		t.Error("missing data field (serialized JSON)")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/go-trace-runner && go test -v ./internal/stream/ -run Test`
Expected: FAIL — package not found

- [ ] **Step 3: Implement Redis stream integration**

```
services/go-trace-runner/internal/stream/stream.go
```
```go
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

// QueueKey returns the Redis list key for Go entrypoint jobs.
func QueueKey(repo string) string {
	return fmt.Sprintf("entrypoint_queue:%s:go", repo)
}

// CacheKey returns the Redis key for trace caching.
func CacheKey(commitSha, stableID string) string {
	return fmt.Sprintf("trace_cache:%s:%s", commitSha, stableID)
}

// ToStreamFields converts a RawTrace to a Redis stream field map.
func ToStreamFields(trace assembler.RawTrace) map[string]interface{} {
	data, _ := json.Marshal(trace)
	return map[string]interface{}{
		"entrypoint_stable_id": trace.EntrypointStableID,
		"repo":                 trace.Repo,
		"commit_sha":           trace.CommitSha,
		"data":                 string(data),
	}
}

// EmitRawTrace writes a RawTrace to stream:raw-traces via XADD.
func EmitRawTrace(ctx context.Context, rdb *redis.Client, trace assembler.RawTrace) error {
	return rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: StreamKey,
		Values: ToStreamFields(trace),
	}).Err()
}

// IsCached checks if a trace has already been generated for this commit+entrypoint.
func IsCached(ctx context.Context, rdb *redis.Client, commitSha, stableID string) bool {
	exists, _ := rdb.Exists(ctx, CacheKey(commitSha, stableID)).Result()
	return exists > 0
}

// MarkCached writes a cache entry with 24h TTL.
func MarkCached(ctx context.Context, rdb *redis.Client, commitSha, stableID string) error {
	return rdb.Set(ctx, CacheKey(commitSha, stableID), "1", CacheTTL).Err()
}

// ConsumeJobs blocks on BLPOP for Go entrypoint jobs. Returns parsed job JSON.
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
	return result[1], nil // result[0] is key, result[1] is value
}
```

- [ ] **Step 4: Run stream tests**

Run: `cd services/go-trace-runner && go mod tidy && go test -v ./internal/stream/`
Expected: PASS

- [ ] **Step 5: Update cmd/main.go to add Redis BLPOP consumer goroutine**

Add to `services/go-trace-runner/cmd/main.go` — a background goroutine that:
1. Calls `stream.ConsumeJobs()` in a loop (BLPOP with 5s timeout)
2. For each job: check cache, call instrumenter API, execute binary, collect events, assemble RawTrace, call `stream.EmitRawTrace()`, mark cached
3. Runs until context is cancelled on shutdown

```go
// Add to main() before ready.Store(true):
go func() {
	rdb := redis.NewClient(&redis.Options{Addr: redisAddr})
	for {
		select {
		case <-ctx.Done():
			return
		default:
			jobJSON, err := stream.ConsumeJobs(ctx, rdb, []string{"*"}, 5*time.Second)
			if err != nil {
				continue // timeout or error, retry
			}
			// Parse job, check cache, instrument, execute, emit
			processJob(ctx, rdb, sessionsDir, jobJSON)
		}
	}
}()
```

- [ ] **Step 6: Commit**

```bash
git add services/go-trace-runner/internal/stream/ services/go-trace-runner/cmd/main.go
git commit -m "feat(go-trace-runner): Redis BLPOP consumer, XADD emission, and trace caching"
```

---

## Task 10: TraceEvent Model Update and Entrypoint Discoverer Changes

**Files:**
- Modify: `services/trace-runner/app/models.py`
- Modify: `services/entrypoint-discoverer/app/models.py`
- Modify: `services/entrypoint-discoverer/app/discoverer.py`
- Modify: `services/entrypoint-discoverer/app/queue.py`
- Modify: `services/entrypoint-discoverer/tests/test_discoverer.py`

- [ ] **Step 1: Update TraceEvent model with optional fields**

File: `services/trace-runner/app/models.py`

Add optional fields after existing `exc_type` field:

```python
    goroutine_id: Optional[int] = None
    span_id: Optional[int] = None
    args: Optional[dict] = None
    return_val: Optional[Any] = None
```

- [ ] **Step 2: Run existing trace-runner tests to verify no regression**

Run: `cd services/trace-runner && python -m pytest tests/ -v`
Expected: All existing tests pass (new fields are optional with defaults)

- [ ] **Step 3: Add language field to EntrypointJob model**

File: `services/entrypoint-discoverer/app/models.py`

Add to `EntrypointJob`:
```python
    language: str = "python"
```

- [ ] **Step 4: Update discoverer.py with Go entrypoint patterns**

File: `services/entrypoint-discoverer/app/discoverer.py`

Add Go patterns to the Neo4j query. After the existing Python WHERE clauses, add
name-based patterns only (framework-based patterns deferred until Go parser extension):

```python
    # Go patterns (name-based — no parser extension required)
    OR (n.type = 'function' AND n.name STARTS WITH 'Test')
    OR (n.type = 'method' AND n.name = 'ServeHTTP')
    OR (n.type = 'function' AND n.name = 'main')
```

Add language detection based on file_path:

```python
def detect_language(file_path: str) -> str:
    if file_path.endswith(".go"):
        return "go"
    return "python"
```

Apply to each discovered entrypoint before queuing.

- [ ] **Step 5: Update queue.py for language-aware routing**

File: `services/entrypoint-discoverer/app/queue.py`

Modify the queue key to append `:go` for Go entrypoints:

```python
def queue_key(repo: str, language: str = "python") -> str:
    key = f"entrypoint_queue:{repo}"
    if language == "go":
        key += ":go"
    return key
```

Update the `push_jobs` function to use language-aware key.

- [ ] **Step 6: Write test for Go entrypoint detection**

File: `services/entrypoint-discoverer/tests/test_discoverer.py`

```python
def test_detect_language_go():
    from app.discoverer import detect_language
    assert detect_language("handlers/order.go") == "go"
    assert detect_language("tests/test_auth.py") == "python"
    assert detect_language("cmd/main.go") == "go"

def test_entrypoint_job_language_default():
    from app.models import EntrypointJob
    job = EntrypointJob(stable_id="x", name="test", file_path="f.py", priority=1, repo="r")
    assert job.language == "python"

def test_entrypoint_job_language_go():
    from app.models import EntrypointJob
    job = EntrypointJob(stable_id="x", name="TestLogin", file_path="f.go", priority=1, repo="r", language="go")
    assert job.language == "go"
```

- [ ] **Step 7: Run entrypoint-discoverer tests**

Run: `cd services/entrypoint-discoverer && python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 8: Commit**

```bash
git add services/trace-runner/app/models.py services/entrypoint-discoverer/
git commit -m "feat(pipeline): add Go support to entrypoint discoverer and extend TraceEvent model"
```

---

## Task 11: Docker Compose Integration

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add go-instrumenter and go-trace-runner to docker-compose.yml**

Add after existing service definitions:

```yaml
  go-instrumenter:
    build:
      context: ./services/go-instrumenter
      dockerfile: Dockerfile
    ports:
      - "8098:8098"
    volumes:
      - ~/workspaces:/repos:ro
      - /tmp/go-trace-sessions:/tmp/sessions
      - ./services/tracert:/app/tracert:ro
    environment:
      - PORT=8098
      - SESSIONS_DIR=/tmp/sessions
      - TRACERT_PATH=/app/tracert
      - REDIS_URL=redis://redis:6379
    networks:
      - tersecontext
    depends_on:
      - redis

  go-trace-runner:
    build:
      context: ./services/go-trace-runner
      dockerfile: Dockerfile
    ports:
      - "8099:8099"
    volumes:
      - /tmp/go-trace-sessions:/tmp/sessions
    environment:
      - PORT=8099
      - SESSIONS_DIR=/tmp/sessions
      - REDIS_URL=redis://redis:6379
      - INSTRUMENTER_URL=http://go-instrumenter:8098
    networks:
      - tersecontext
    depends_on:
      - redis
      - go-instrumenter
```

- [ ] **Step 2: Verify docker-compose config is valid**

Run: `docker-compose config --quiet`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(infra): add go-instrumenter and go-trace-runner to docker-compose"
```

---

## Task 12: End-to-End Integration Test

**Files:**
- Create: `tests/test_go_tracing_e2e.py`
- Create: `scripts/push_go_job.py`

- [ ] **Step 1: Create manual queue push script**

```
scripts/push_go_job.py
```
```python
#!/usr/bin/env python3
"""Push a Go entrypoint job to Redis for testing."""
import json
import sys
import redis

r = redis.Redis(host="localhost", port=6379)

job = {
    "stable_id": "sha256:test_stable_id",
    "name": sys.argv[1] if len(sys.argv) > 1 else "TestExample",
    "file_path": sys.argv[2] if len(sys.argv) > 2 else "example_test.go",
    "priority": 2,
    "repo": sys.argv[3] if len(sys.argv) > 3 else "test-repo",
    "language": "go",
}

key = f"entrypoint_queue:{job['repo']}:go"
r.rpush(key, json.dumps(job))
print(f"Pushed job to {key}: {json.dumps(job, indent=2)}")
```

- [ ] **Step 2: Create integration test**

```
tests/test_go_tracing_e2e.py
```
```python
"""
End-to-end test for Go dynamic tracing pipeline.
Requires: go-instrumenter (8098) and go-trace-runner (8099) running.
"""
import json
import pytest
import httpx


@pytest.fixture
def instrumenter():
    return httpx.Client(base_url="http://localhost:8098", timeout=60)


@pytest.fixture
def trace_runner():
    return httpx.Client(base_url="http://localhost:8099", timeout=60)


def test_instrumenter_health(instrumenter):
    resp = instrumenter.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_trace_runner_health(trace_runner):
    resp = trace_runner.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.skipif(
    not _has_gastown(),
    reason="gastown repo not available at ~/workspaces/gastown",
)
def test_instrument_gastown(instrumenter):
    resp = instrumenter.post("/instrument", json={
        "repo": "gastown",
        "repo_path": "/repos/gastown",
        "commit_sha": "HEAD",
        "entrypoints": ["TestExample"],
        "language": "go",
        "boundary_patterns": ["*.Handler*", "db.*"],
        "include_deps": [],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"]
    assert data["instrumented_funcs"] > 0


def _has_gastown():
    import os
    return os.path.exists(os.path.expanduser("~/workspaces/gastown"))
```

- [ ] **Step 3: Run integration tests (services must be running)**

Run: `python -m pytest tests/test_go_tracing_e2e.py -v`
Expected: Health checks pass; gastown test passes if repo is available

- [ ] **Step 4: Commit**

```bash
git add tests/test_go_tracing_e2e.py scripts/push_go_job.py
git commit -m "test(e2e): Go tracing integration tests and manual queue push script"
```
