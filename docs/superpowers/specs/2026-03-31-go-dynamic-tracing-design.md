# Go Dynamic Tracing Pipeline — Design Spec

**Date:** 2026-03-31
**Branch:** go_live_trace
**Status:** Approved

## Goal

Given a Go repo, produce runtime call graphs, execution traces, and argument/return
snapshots that feed into behavior spec generation. The output must be compatible with
the existing downstream pipeline (trace-normalizer, graph-enricher, spec-generator)
via the shared `RawTrace` schema on `stream:raw-traces`.

## Approach: Coverage-Guided AST Rewriting (Hybrid)

Three-phase approach chosen after evaluating five alternatives:

1. **Coverage pre-scan** — Run `go test -coverprofile` to identify exercised functions.
   Fast, built-in, eliminates instrumenting dead code.
2. **Targeted AST rewrite** — Use `go/ast` + `go/parser` to inject tracing calls only
   into reachable functions. Lightweight call graph tracing everywhere, selective arg
   capture at configured boundary functions.
3. **Trace execution** — Build and run the rewritten source, collect structured trace
   events via Unix socket, emit `RawTrace` to the existing stream.

### Why not the alternatives?

- **Pure AST rewriting** — Works but wastes effort instrumenting unreachable code.
- **Delve-based tracing** — 50-100x overhead per traced function. Only viable for
  targeted debugging, not full call graph capture.
- **runtime/trace + pprof** — Gives goroutine/scheduling data but not arbitrary
  function entry/exit. Can't produce call graphs.
- **Coverage profiles alone** — Line-level execution data but no call ordering,
  arguments, or return values.

### Design constraints

- **Overhead budget:** <20% (dedicated trace runs, not production)
- **Arg capture:** Selective at boundary functions only (HTTP handlers, DB calls,
  external clients). Full call graph for all other functions.
- **Dependency tracing:** Configurable. Default to first-party only, allow a config
  list of third-party packages to include.
- **Entrypoint types:** Test functions (`Test*`) via test-driven tracing, plus HTTP
  handlers and CLI commands via synthetic request generation.

---

## Component Architecture

### New components

| Component | Type | Port | Responsibility |
|-----------|------|------|----------------|
| **go-instrumenter** | Service | 8098 | AST-parses target repo, rewrites source with tracing hooks, builds instrumented binary |
| **go-trace-runner** | Service | 8099 | Executes instrumented binary (tests + synthetic requests), collects trace events, emits RawTrace |
| **trace-runtime (tracert)** | Go library | — | Lightweight tracing runtime injected into instrumented binary |

### Modified components

| Component | Change |
|-----------|--------|
| **entrypoint-discoverer** (8092) | Add Go entrypoint patterns, `language` field on jobs, route Go jobs to separate queue |
| **entrypoint-discoverer models** | Add `language: str = "python"` default field to `EntrypointJob` |
| **Go parser** (`extractor_go.py`) | Detect framework route registrations and store as `framework_annotations` on nodes |
| **TraceEvent model** | Add optional `goroutine_id`, `span_id`, `args`, `return_val` fields |

### Unchanged components

trace-normalizer (8095), graph-enricher (8096), spec-generator (8097) — all consume
the same `RawTrace` / `ExecutionPath` schemas. New optional fields on `TraceEvent`
are backward-compatible; the normalizer ignores fields it doesn't use. The goroutine_id
and span_id fields are available for future normalizer enhancements (async call tree
reconstruction) but are not required for the current pipeline.

### Data flow

```
entrypoint-discoverer
        │
        ▼
  Redis: entrypoint_queue:{repo}:go
        │
        ▼
  go-instrumenter ──→ instrumented binary ──→ go-trace-runner
  (AST rewrite +       (in /tmp/sessions/)     (execute, collect events)
   go build)                                          │
                                                      ▼
                                            stream:raw-traces
                                                      │
                                    (existing pipeline unchanged)
                                                      │
                                ┌─────────────────────┼──────────────┐
                                ▼                     ▼              ▼
                        trace-normalizer      graph-enricher   spec-generator
```

---

## Component 1: Trace Runtime Library (tracert)

Go package injected as a dependency into the instrumented binary. This is the core
of the system.

### API surface

```go
package tracert

// Enter — called at function entry. Returns span ID for matching exit.
// Interior functions: pushes onto per-goroutine call stack (~5ns).
// Boundary functions: also captures args via serialization (~200-500ns).
func Enter(funcID string, args ...any) uint64

// Exit — called via defer at function entry point.
// Pops from call stack, records duration.
// Boundary functions: captures return values.
// Detects panics via recover() internally — if a panic is in flight,
// emits an "exception" event and re-panics to preserve the stack trace.
func Exit(spanID uint64, returns ...any)

// Go — wraps goroutine creation to propagate trace context.
// Replaces `go func()` calls to track parent-child goroutine relationships.
func Go(fn func())

// Init — called once from instrumented main/TestMain.
// Opens Unix socket to trace collector, starts event buffer.
func Init(socketPath string)

// Flush — called at end of trace run. Drains event buffers.
func Flush()
```

### Per-goroutine tracking

Each goroutine gets a monotonic trace-local ID assigned when spawned through the
`Go()` wrapper. Parent-child relationships are tracked, giving us a call graph
across goroutines (not just flat IDs).

**Implementation note:** Go intentionally has no goroutine-local storage. Options:
- Use `github.com/petermattis/goid` (assembly stubs to read goroutine ID, ~2ns)
- Use `runtime.Stack()` to parse goroutine ID from header (~1-2us, too slow for interior calls)
- Use `go:linkname` hack for `runtime.getg()` (fragile across Go versions)

**Chosen approach:** Use `goid` library for the goroutine ID lookup (~2ns overhead),
combined with a `sync.Map` for goroutine metadata (parent ID, call stack). The `Go()`
wrapper allocates a new context inheriting the parent's trace ID. This is a known
technical risk — if `goid` breaks on a future Go version, we fall back to
`runtime.Stack()` with caching (first call per goroutine is ~1us, subsequent calls
return cached value).

### Event emission

Events are buffered per-goroutine in a pre-allocated ring buffer (no GC pressure),
flushed to a Unix domain socket as newline-delimited JSON.

**Wire format** (internal, between instrumented binary and collector over Unix socket):
```json
{"t":"call","f":"auth.Authenticate","file":"auth/service.go","l":34,"ts":0,"g":1,"s":42}
{"t":"ret","f":"auth.Authenticate","file":"auth/service.go","l":52,"ts":28,"g":1,"s":42}
{"t":"panic","f":"auth.Authenticate","file":"auth/service.go","l":48,"ts":25,"g":1,"s":42,"exc":"runtime error: index out of range"}
{"t":"call","f":"sql.DB.Query","file":"auth/service.go","l":40,"ts":5,"g":1,"s":43,"args":{"query":"SELECT * FROM users WHERE id=$1","params":["uid-123"]}}
```

Wire fields: `t`=type, `f`=function, `file`, `l`=line, `ts`=timestamp_ms, `g`=goroutine_id,
`s`=span_id, `args`/`ret` only on boundary functions, `exc` on panic events.

**Collector mapping to RawTrace TraceEvent:** The go-trace-runner collector transforms
wire events before emitting to `stream:raw-traces`:
- `"ret"` → `"return"`, `"panic"` → `"exception"` (matches existing `TraceEvent.type` enum)
- `g` → `goroutine_id` (optional field), `s` → `span_id` (optional field)
- `args`/`ret` → `args`/`return_val` (optional fields)
- `exc` → `exc_type` (existing field on TraceEvent)

This ensures the downstream normalizer receives valid `TraceEvent` objects.

### Overhead budget

| Operation | Cost | Frequency |
|-----------|------|-----------|
| `Enter` (interior) | ~5ns (atomic increment + stack push) | Every function call |
| `Exit` (interior) | ~5ns (stack pop + duration calc) | Every function return |
| `Enter` (boundary) | ~200-500ns (arg serialization) | Configured functions only |
| `Go` wrapper | ~50ns (allocate goroutine context) | Each `go` statement |
| Socket write | Batched, async | Every ~1000 events |

Total overhead stays well under 20% for typical workloads.

---

## Component 2: Go Instrumenter Service (8098)

Takes a Go repo + list of entrypoints, produces an instrumented binary.

### Stage 1: Coverage pre-scan (optional, for test entrypoints)

```bash
go test -coverprofile=cover.out -covermode=atomic -run=TestTargetFunc ./...
go tool cover -func=cover.out
```

Output: set of `{file, funcName}` pairs that were exercised. Used to skip
instrumenting dead code in Stage 2.

### Stage 2: AST rewriting

Parse each `.go` file with `go/parser`, walk the AST, inject tracing calls.

**Interior functions (all functions):**
```go
// BEFORE:
func ProcessOrder(ctx context.Context, order Order) error {
    // body...
}

// AFTER:
func ProcessOrder(ctx context.Context, order Order) error {
    __span := tracert.Enter("myrepo/orders.ProcessOrder")
    defer func() { tracert.Exit(__span) }()
    // body...
}
```

**Boundary functions (configured patterns like `*.Handler*`, `db.*`):**
```go
// AFTER:
func (h *OrderHandler) Create(w http.ResponseWriter, r *http.Request) {
    __span := tracert.Enter("myrepo/handlers.OrderHandler.Create", w, r)
    defer func() { tracert.Exit(__span) }()
    // body...
}
```

**Goroutine spawning (`go` statements):**

Phase 2 handles these patterns:
```go
// Simple function call
go processAsync(item)          → tracert.Go(func() { processAsync(item) })

// Anonymous function (very common)
go func() { doWork() }()      → tracert.Go(func() { doWork() })

// Anonymous function with args
go func(x int) { use(x) }(v)  → tracert.Go(func() { func(x int) { use(x) }(v) })

// Method value
go obj.Method(args)            → tracert.Go(func() { obj.Method(args) })
```

Deferred to future: goroutines launched via `errgroup.Go()`, `pool.Submit()`,
or other wrapper libraries (these appear as regular function calls in the trace,
the goroutine creation happens inside the library).

**Panic recovery:**

Panic detection is handled inside `Exit` itself using `recover()`. If a panic is
in flight, `Exit` emits an `"exception"` event with the panic value, then re-panics
to preserve the original stack trace. This means the simple `defer tracert.Exit(__span)`
pattern works for both normal returns and panics — no separate `ExitPanic` function
or special defer wrapper needed.

For this to work, `Exit` must be called inside a `defer func()` closure (not as a
bare `defer tracert.Exit(__span)`) since `recover()` only works in the deferred
function itself. The AST rewriter injects:
```go
defer func() { tracert.Exit(__span) }()
```

**Skipped:**
- Files with 0% coverage from Stage 1
- Generated files (`// Code generated` header)
- CGo files (contain interleaved C code, will cause AST parse failures)
- Files with restrictive build tags (`//go:build ignore`)
- Third-party packages (unless in configurable include list)

**Generics note:** Go 1.18+ generic functions are instrumented like any other function.
All instantiations of a generic function share the same `funcID`. Type-parameter-aware
tracing (distinguishing `Process[int]` from `Process[string]`) is a future enhancement.

### Stage 3: Build

```bash
# Inject tracert dependency
go mod edit -require github.com/tersecontext/tracert@v0.0.0
go mod edit -replace github.com/tersecontext/tracert=<local_path>

# Build
go build -o /tmp/sessions/{session_id}/binary ./cmd/server
# OR for test entrypoints:
go test -c -o /tmp/sessions/{session_id}/binary ./pkg/...
```

### HTTP API

```
POST /instrument
{
  "repo": "gastown",
  "repo_path": "/repos/gastown",
  "commit_sha": "a4f91c",
  "entrypoints": ["TestLogin", "OrderHandler.Create"],
  "language": "go",
  "boundary_patterns": ["*.Handler*", "db.*", "http.*"],
  "include_deps": []
}

→ 200 OK
{
  "session_id": "uuid",
  "binary_path": "/tmp/sessions/{session_id}/binary",
  "binary_type": "test" | "server",
  "instrumented_funcs": 247,
  "skipped_funcs": 89,
  "coverage_filtered": 62
}
```

### Standard endpoints

Both new services expose `GET /health`, `GET /ready`, `GET /metrics` per project convention.

### Key decisions

- **Written in Go** — requires `go/ast`, `go/parser`, `go/printer`, `go build`
- **Stateful** — produces a binary artifact persisting until consumed. 1-hour TTL cleanup.
  Cleanup skips directories with `.lock` files (created by the trace-runner during execution).
- **Workspace isolation** — each session gets its own temp directory with source copy

---

## Component 3: Go Trace Runner Service (8099)

Executes instrumented binaries, collects trace events, emits `RawTrace`.

### Execution modes

**Mode A: Test entrypoints**
```bash
/tmp/sessions/{session_id}/binary -test.run=TestLogin -test.timeout=30s
```
`Init()` called from injected `TestMain`. Events flow over Unix socket during execution.

**Mode B: Server entrypoints (synthetic requests)**

1. Start instrumented server binary
2. Wait for readiness (poll `/health` or watch stdout)
3. Send synthetic requests derived from route signatures in the static graph:
   - Correct HTTP method from route registration
   - Path params filled with type-appropriate placeholders
   - Minimal JSON bodies from struct definitions
4. Collect trace events during request processing
5. SIGTERM server, wait for `Flush()`

### Synthetic request generation

```go
type SyntheticRequest struct {
    Method  string            // from route registration
    Path    string            // /users/{id} → /users/test-1
    Headers map[string]string // Content-Type, Auth if required
    Body    []byte            // minimal valid JSON from struct schema
}
```

Source data from entrypoint-discoverer + static graph in Neo4j.

### Event collection

```
┌─────────────────────┐     Unix socket      ┌──────────────┐
│  Instrumented binary │ ──────────────────→  │  Collector    │
│  (tracert runtime)   │   JSON lines         │  goroutine    │
└─────────────────────┘                       └──────┬───────┘
                                                     │
                                              Buffer + transform
                                                     │
                                                     ▼
                                              RawTrace assembly
                                                     │
                                              XADD stream:raw-traces
```

Collector reads JSON lines from Unix socket, groups by goroutine ID, assembles
`RawTrace` per entrypoint on flush, maps function names to `stable_id` using
the same hash function as the parser: `sha256(f"{repo}:{file_path}:{node_type}:{qualified_name}")`.
The stable_id is the hex digest of this hash, not the human-readable composite string.

### RawTrace output (identical schema to Python)

```json
{
  "entrypoint_stable_id": "sha256:a7f3b2c1d4e5...",
  "commit_sha": "a4f91c",
  "repo": "gastown",
  "duration_ms": 142,
  "events": [
    {"type": "call", "fn": "OrderHandler.Create", "file": "handlers/order.go", "line": 28, "timestamp_ms": 0},
    {"type": "call", "fn": "db.QueryRow", "file": "handlers/order.go", "line": 35, "timestamp_ms": 2},
    {"type": "return", "fn": "db.QueryRow", "file": "handlers/order.go", "line": 35, "timestamp_ms": 18},
    {"type": "return", "fn": "OrderHandler.Create", "file": "handlers/order.go", "line": 61, "timestamp_ms": 142}
  ]
}
```

### Caching

- Key: `trace_cache:{commit_sha}:{stable_id}`, TTL: 24 hours
- Check before executing, skip if cached

### Timeouts and safety

- Test execution: 30s (configurable)
- Server mode: 60s total (startup + requests + shutdown)
- Binary panics: collector retains all pre-panic events
- Sandboxed: no network access except Unix socket

### HTTP API

```
POST /run
{
  "session_id": "uuid",
  "entrypoints": [{"stable_id": "sha256:...", "name": "TestLogin", "type": "test"}],
  "commit_sha": "a4f91c",
  "repo": "gastown",
  "timeout_s": 30
}
→ 202 Accepted {"trace_id": "uuid", "status": "running"}

GET /run/{trace_id}/status
→ {"status": "completed", "events_emitted": 1247, "duration_ms": 3400}
```

---

## Entrypoint Discoverer Changes (8092)

### New Go entrypoint patterns

**Note:** Go has no decorators. The Go parser (`extractor_go.py`) must be extended to
detect framework route registrations by analyzing call expressions in function bodies
(e.g., `router.GET("/path", handlerFunc)`) and storing them as `framework_annotations`
on the handler node in Neo4j. This parser change is a prerequisite for framework-specific
entrypoint discovery.

```cypher
MATCH (n:Node {repo: $repo, active: true})
WHERE
  // Python patterns (existing)
  n.name STARTS WITH 'test_'
  OR n.decorators CONTAINS 'app.route'
  OR n.decorators CONTAINS 'router.'
  OR n.decorators CONTAINS 'click.command'
  // Go patterns — name-based (work today)
  OR (n.type = 'function' AND n.name STARTS WITH 'Test')
  OR (n.type = 'method' AND n.name = 'ServeHTTP')
  OR (n.type = 'function' AND n.name = 'main')
  // Go patterns — framework-based (requires parser extension)
  OR n.framework_annotations CONTAINS 'gin.GET'
  OR n.framework_annotations CONTAINS 'gin.POST'
  OR n.framework_annotations CONTAINS 'mux.HandleFunc'
  OR n.framework_annotations CONTAINS 'chi.Get'
  OR n.framework_annotations CONTAINS 'cobra.Command'
RETURN n.stable_id, n.name, n.file_path, n.type
```

### Job routing

Go jobs use a new queue key; the existing Python queue is unchanged:
- `entrypoint_queue:{repo}:go` — **new**, consumed by go-trace-runner
- `entrypoint_queue:{repo}` — **unchanged**, consumed by existing Python trace-runner

This avoids breaking the existing Python trace-runner which uses BLPOP on
`entrypoint_queue:{repo}`. The entrypoint-discoverer routes Go jobs to the new
queue based on the file extension (.go) or node language metadata.

Job payload adds `language` field (defaulted so existing Python jobs are unchanged):
```json
{
  "stable_id": "sha256:...",
  "name": "OrderHandler.Create",
  "file_path": "handlers/order.go",
  "priority": 2,
  "repo": "gastown",
  "language": "go"
}
```

The `EntrypointJob` model gets `language: str = "python"` — backward-compatible default.

---

## docker-compose additions

```yaml
go-instrumenter:
  build:
    context: .
    dockerfile: docker/go.Dockerfile
  ports: ["8098:8098"]
  volumes:
    - ~/workspaces:/repos:ro
    - /tmp/go-trace-sessions:/tmp/sessions
  environment:
    - REDIS_URL=redis://redis:6379
    - NEO4J_URI=bolt://neo4j:7687
  networks: [tersecontext]

go-trace-runner:
  build:
    context: .
    dockerfile: docker/go.Dockerfile
  ports: ["8099:8099"]
  volumes:
    - /tmp/go-trace-sessions:/tmp/sessions
  environment:
    - REDIS_URL=redis://redis:6379
    - INSTRUMENTER_URL=http://go-instrumenter:8098
  networks: [tersecontext]
```

Shared `/tmp/sessions` volume: instrumenter writes binary, runner reads and executes.

---

## Phased implementation plan

### Phase 1: Trace runtime library (tracert)
- `Enter` / `Exit` with per-goroutine call stacks
- `Go` wrapper for goroutine context propagation
- Unix socket event emission with ring buffer
- Unit tests with synthetic call graphs

### Phase 2: Go instrumenter — AST rewriting
- `go/ast` parser + rewriter for function entry/exit injection
- `go` statement rewriting to `tracert.Go()`
- Boundary function pattern matching + arg capture injection
- `go build` / `go test -c` compilation stage
- Test against gastown repo

### Phase 3: Go instrumenter — Coverage pre-scan
- `go test -coverprofile` integration
- Coverage report parsing to function-level filter
- Dead code elimination from instrumentation set

### Phase 4: Go trace runner — Test execution mode
- Redis queue consumer for Go entrypoints
- Binary execution with Unix socket collection
- RawTrace assembly and emission to `stream:raw-traces`
- Wire format → TraceEvent mapping (`"ret"`→`"return"`, `"panic"`→`"exception"`)
- Cache check/write
- End-to-end test: gastown test → RawTrace → normalizer
- **Note:** Until Phase 6, test with manual Redis queue pushes (scripts/push_go_job.py)

### Phase 5: Go trace runner — Synthetic request mode
- Route signature extraction from Neo4j
- Minimal request generation from struct schemas
- Server lifecycle management (start, ready-check, request, shutdown)
- End-to-end test: gastown HTTP handler → RawTrace

### Phase 6: Go parser extension
- Detect framework route registrations in Go source (gin, chi, mux, cobra)
- Store as `framework_annotations` on Neo4j nodes
- Required for framework-based entrypoint discovery

### Phase 7: Entrypoint discoverer integration
- Go entrypoint pattern queries (name-based + framework-based)
- Language-specific queue routing (Go jobs → `entrypoint_queue:{repo}:go`)
- Add `language` field to `EntrypointJob` model with `"python"` default
- Priority scoring for Go entrypoints

### Proof of concept target

Phases 1-2 + 4 against the gastown repo: instrument gastown's test suite, run it,
produce `RawTrace` events that the existing trace-normalizer can consume. This
validates the full path from Go source → AST rewrite → instrumented binary →
trace events → existing pipeline.

---

## Change summary

| Component | Change | Scope |
|-----------|--------|-------|
| tracert (new) | Go library | ~500 LOC |
| go-instrumenter (new) | Service at 8098 | ~800 LOC |
| go-trace-runner (new) | Service at 8099 | ~600 LOC |
| go-parser extension | Framework annotation detection | ~100 LOC |
| entrypoint-discoverer | Add Go patterns + queue routing + language field | ~80 LOC |
| TraceEvent model | Add optional goroutine/span/args fields | ~10 LOC |
| trace-normalizer | No changes | — |
| graph-enricher | No changes | — |
| spec-generator | No changes | — |
| docker-compose | Add 2 services | ~20 lines |

**Total estimated new code:** ~2,000 LOC Go
**Downstream changes:** ~190 LOC Python (parser extension, entrypoint-discoverer, model updates)

---

## Known technical risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| `goid` library breaks on new Go version | Goroutine tracking fails | Fall back to `runtime.Stack()` with per-goroutine caching |
| AST rewriting misses edge cases (init funcs, type assertions) | Some functions uninstrumented | Incremental coverage — add patterns as discovered |
| Large repos have slow `go build` after rewriting | Instrumenter latency | Only rewrite coverage-filtered files; cache builds per commit |
| Generic function instantiations share funcID | Imprecise call graph for generic code | Acceptable for Phase 2; type-parameter awareness is future work |
