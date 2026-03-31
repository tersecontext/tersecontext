# Instrumenter Implementation + Three Trace Improvements

**Date:** 2026-03-31
**Branch:** python_dynamic_trace_improvement
**Status:** Design approved

---

## Overview

Build the instrumenter's missing execution engine (`/run` endpoint, `sys.settrace` hook, I/O mocking) with three improvements baked in from the start: selective argument capture, coverage-guided filtering, and async tracing.

**Target:** Python 3.12+ only.
**Approach:** Coverage pre-pass owned by trace-runner; everything else in instrumenter. Approach B from brainstorming.

---

## Decisions from brainstorming

| Question | Decision |
|----------|----------|
| Build base instrumenter separately or together with improvements? | Together (Option B) |
| Python version target | 3.12+ only — skip deprecated `sys.set_coroutine_wrapper` |
| Serializer for arg capture | Best-effort recursive, depth 2, 1KB cap |
| Coverage pre-pass scope | Whole test suite per repo/commit, cached in Redis |
| Architecture | Approach B: coverage in trace-runner, instrumentation in instrumenter |

---

## Section 1: Instrumenter — Base Trace Engine + I/O Mocking

### Session state

In-memory dict keyed by `session_id`. Each session holds:

- `trace_events: list[TraceEvent]` — accumulated events
- `patches: list[PatchSpec]` — active mock patches from PATCH_CATALOG
- `capture_args: list[str]` — fnmatch patterns for arg capture
- `coverage_filter: set[str] | None` — file paths with any executed lines from coverage pre-pass
- `task_id_counter: int` — monotonic counter for async task IDs
- `tempdir: str` — redirect target for file writes

### The trace function

`trace_func(frame, event, arg)`:

1. On `call`/`return`/`exception`: check `coverage_filter` — if set and `filename` not in it, skip
2. Build `TraceEvent` with type, fn, file, line, timestamp
3. If `capture_args` patterns match (via pre-compiled regex): on `call`, serialize `frame.f_locals` with depth-2 recursive serializer (1KB cap); on `return`, serialize `arg`
4. Stamp current `task_id` from context variable
5. Append to session's event list

### I/O mocking

Uses `unittest.mock.patch` as context managers:

- **DB targets** (sqlalchemy, psycopg2) → log SQL string as event, return mock cursor with empty results
- **HTTP targets** (httpx, requests) → log method+URL as event, return mock 200 response
- **`builtins.open`** in write modes → redirect to session tempdir, log the path

### `/run` endpoint

```
POST /run { session_id: str }
→ { events: list[TraceEvent], duration_ms: float }
```

1. Look up session by ID
2. Enter all mock patches as context managers
3. Install `sys.settrace(trace_func)`
4. Execute the entrypoint with 30s timeout:
   - **Sync entrypoints:** use `signal.alarm` + SIGALRM handler
   - **Async entrypoints:** use `asyncio.wait_for(coro, timeout=30)` inside `asyncio.run()`
5. Uninstall trace, exit patches
6. Return collected events + wall-clock duration
7. Clean up session state

### Session eviction

Sessions are stored in-memory with a creation timestamp. A background task runs every 30s and evicts sessions older than 60s that were never `/run`. On eviction, the session's tempdir is removed. Maximum 100 concurrent sessions; `/instrument` returns 503 if limit reached.

### `/instrument` changes

Accept two new optional fields:

- `capture_args: list[str] = []`
- `coverage_filter: list[str] | None = None`

---

## Section 2: Async Tracing (Python 3.12+)

### Problem

`sys.settrace` follows synchronous calls but when a coroutine awaits another coroutine, the trace function sees the awaited function as a separate top-level call. `asyncio.create_task()` spawns concurrent work invisible to the call stack.

### Task ID tracking

A `contextvars.ContextVar` holds the current `task_id`. Root entrypoint gets `task_id=0`. Each spawned task gets a monotonically incrementing ID from the session's counter.

### Hooks installed during `/run`

1. **Wrap `asyncio.create_task()`:** Increment counter, assign new ID, copy current `task_id` as `parent_task_id`, emit `async_call` event, wrap coroutine to emit `async_return` on completion.

2. **Wrap `asyncio.gather()`:** Same pattern — each gathered coroutine gets its own `task_id`, all share same parent.

3. **`sys.set_asyncgen_hooks`:** Register `firstiter` and `finalizer` callbacks to track async generator lifecycle.

### New event types

- `async_call` — emitted when a task is created, carries `task_id` and `parent_task_id`
- `async_return` — emitted when a spawned task completes

### Trace function amendment

On every `call`/`return`/`exception` event, stamp current `task_id` from context variable onto TraceEvent.

### Entrypoint execution

If entrypoint is a coroutine function, run with `asyncio.run()` and use `asyncio.wait_for()` for the 30s timeout (not `signal.alarm`, which interacts poorly with the event loop).

### Cleanup

Restore original `asyncio.create_task`, `asyncio.gather`, remove asyncgen hooks.

---

## Section 3: Selective Argument Capture

### Configuration

`capture_args` patterns originate from `/instrument` request. Default set: `["test_*", "*_handler", "*_view", "db_*"]`. These are fnmatch patterns matched against the bare function name (not the qualified module path). Overridable per-request. Defaults are intentionally narrow to avoid matching framework internals (e.g., `logging.Handler`).

### The serializer

```python
safe_serialize(obj, max_depth=2, max_bytes=1024) -> str
```

- **Depth 0-2:** Walk recursively
  - `dict` → serialize keys and values
  - `list`/`tuple`/`set` → serialize elements
  - Pydantic `BaseModel` / dataclass → convert to dict, serialize
  - Primitives (`str`, `int`, `float`, `bool`, `None`) → direct JSON
  - Everything else → `"<ClassName>"`
- **Depth exceeded:** `"<ClassName>"`
- **Cycles:** Track `id()` of visited objects, emit `"<circular>"`
- **Size cap:** After JSON encoding, truncate to 1KB with `"...[truncated]"` suffix
- **Exceptions:** Return `"<unserializable: ErrorMessage>"`

### Integration into trace_func

- On `call`, if function matches pre-compiled regex: serialize `frame.f_locals` → `TraceEvent.args`
- On `return` for matching function: serialize `arg` → `TraceEvent.return_val`

### Pattern matching optimization

Pre-compile fnmatch patterns into single `re.Pattern` at session creation (translate → regex, join with `|`). One `re.match()` per call event.

---

## Section 4: Coverage-Guided Filtering

### Ownership

Trace-runner owns the coverage pre-pass. Runs once per `(repo, commit_sha)`.

### Flow

1. Trace-runner pops job from queue
2. Check Redis for `coverage:{repo}:{commit_sha}`
3. **Cache miss:**
   - Run `pytest --cov --cov-report=json` in repo checkout, 120s timeout
   - Parse JSON report: extract file paths that have any `executed_lines` (coverage.py reports at line granularity, not function granularity). Flatten to a set of file path strings.
   - Extract `totals.percent_covered` as `coverage_pct`
   - Store file set + `coverage_pct` in Redis with 24h TTL
4. **Cache hit:** Deserialize the set and `coverage_pct`
5. Pass `coverage_filter` (set of file paths) to instrumenter's `/instrument` request
6. Instrumenter trace function skips events for functions whose `frame.f_code.co_filename` is not in the filter set

### Fallback

If `pytest --cov` fails, log warning and proceed with `coverage_filter=None` (trace everything).

### Coverage percentage

JSON report's `totals.percent_covered` → `RawTrace.coverage_pct` → spec-generator writes to `behavior_specs.branch_coverage`.

### Filter granularity

File-level. The `coverage.py` JSON report provides `executed_lines` per file, not per function. If a file has any executed lines, all functions in that file pass the filter. This is coarser than function-level but avoids the complexity of cross-referencing line ranges with AST function definitions.

### Note on `sys.setprofile`

The CLAUDE.md snippet shows both `sys.settrace` and `sys.setprofile`. This design uses only `sys.settrace`. `sys.setprofile` adds `c_call`/`c_return`/`c_exception` for C-extension calls, which are not useful for tracing user-authored Python code. Omitting it reduces overhead.

---

## Section 5: Model Changes

All new fields are optional for backward compatibility.

### TraceEvent (trace-runner models.py AND trace-normalizer models.py)

There are two copies of `TraceEvent`: the trace-runner version uses `type: str`, the trace-normalizer version uses `type: Literal["call", "return", "exception"]`. Both must be updated.

**New fields (both copies):**
```python
task_id: Optional[int] = None          # async task correlation
args: Optional[str] = None             # serialized arguments (JSON string, ≤1KB)
return_val: Optional[str] = None       # serialized return value (JSON string, ≤1KB)
```

**trace-normalizer TraceEvent type field must be widened:**
```python
type: Literal["call", "return", "exception", "async_call", "async_return"]
```

### RawTrace (trace-runner models.py)

```python
coverage_pct: Optional[float] = None   # from pytest --cov totals
```

### ExecutionPath (trace-normalizer)

```python
coverage_pct: Optional[float] = None   # passthrough from RawTrace
```

### InstrumentRequest (instrumenter models.py)

```python
capture_args: list[str] = []
coverage_filter: Optional[list[str]] = None
```

### Downstream impact

- **trace-normalizer:** If `task_id` present on events, partition by task, build sub-trees, link via `async_call`/`async_return`. If absent, existing logic unchanged. The normalizer's `CallNode` feeds into the spec-generator's `CallSequenceItem` (which adds `name` and `qualified_name` fields). Async sub-trees are flattened into the `CallNode` list before emission — the spec-generator's `CallSequenceItem` does not need structural changes, only correct hop numbering from the flattened tree.
- **graph-enricher:** No changes.
- **spec-generator:** Uses `coverage_pct` when available. In `store.py`, `upsert_spec` checks `path.coverage_pct` first; if present, uses it for `branch_coverage`. If absent, falls back to `_compute_branch_coverage(path.call_sequence)`. Renders arg examples in PATH section when present.

---

## Section 6: Trace-Normalizer Changes for Async

### Current behavior

Walks events linearly, stack-based `call`/`return` matching.

### New behavior when `task_id` present

1. Partition events by `task_id`
2. Build call tree per task (same stack algorithm)
3. Link via `async_call`/`async_return`: child task's tree inserted as sub-tree at the `async_call` point in parent
4. Flatten linked tree into ordered `CallNode` list with correct hop numbering

### Fallback

No `task_id` → existing linear algorithm unchanged.

### No changes needed

- Frequency aggregation (per-entrypoint regardless of async)
- Side effect classification (already pattern-based on function names)

---

## Section 7: Spec-Generator Enrichment

### coverage_pct in behavior_specs

Prefer pytest-derived `coverage_pct` when available, fall back to frequency-ratio-derived value.

### Argument examples in spec text

When args data exists, render in PATH section:

```
PATH authenticate  (12 runs observed)
  1.  login_handler    1.0    ~4ms    args: {"username": "str", "password": "str"}
  2.  validate_user    0.92   ~2ms
  3a. db_lookup        0.92   ~8ms    args: {"user_id": "int"}
```

Best-effort — only shown for functions matching `capture_args` patterns. Absent arg data renders as before.

No Qdrant schema changes — embedded spec text naturally includes arg examples.

---

## Section 8: Testing Strategy

### Instrumenter (new)

- `test_trace_func` — simple call chain, verify event order and types
- `test_trace_func_coverage_filter` — functions outside filter produce no events
- `test_trace_func_arg_capture` — `args`/`return_val` populated for matches, absent otherwise
- `test_safe_serialize` — depth limiting, cycle detection, truncation, unserializable fallback
- `test_async_hooks` — async function with `create_task`/`gather`, verify `async_call`/`async_return` with correct `task_id` linkage
- `test_async_gather_with_exceptions` — `gather(return_exceptions=True)` and tasks that raise, verify event stream integrity
- `test_async_nested_create_task` — nested `create_task` calls, verify parent chain
- `test_io_mocking` — DB/HTTP/file patches intercept without hitting real systems
- `test_run_endpoint` — integration: `/instrument` then `/run`, verify events

### Trace-runner (additions)

- `test_coverage_prepass` — mock `subprocess.run`, verify JSON parsing and Redis caching
- `test_coverage_cache_hit` — cache used on second call for same commit
- `test_coverage_failure_fallback` — `coverage_filter=None` when pytest fails

### Trace-normalizer (additions)

- `test_async_call_tree` — events with `task_id` and `async_call`/`async_return`, verify linked sub-trees
- `test_mixed_sync_async` — sync events without `task_id` still work

### Spec-generator (additions)

- `test_render_with_args` — arg examples in PATH section
- `test_render_without_args` — backward compatibility
- `test_coverage_pct_passthrough` — `coverage_pct` written to behavior_specs

---

## Files modified

| File | Changes |
|------|---------|
| `services/instrumenter/app/main.py` | `/run` endpoint, session management, session eviction background task |
| `services/instrumenter/app/trace.py` | **New:** trace_func, async hooks, I/O mock setup |
| `services/instrumenter/app/serializer.py` | **New:** safe_serialize with depth/cycle/size handling |
| `services/instrumenter/app/models.py` | `capture_args`, `coverage_filter` on InstrumentRequest |
| `services/instrumenter/app/config.py` | Default `capture_args` patterns |
| `services/trace-runner/app/runner.py` | Coverage pre-pass before `/instrument` call |
| `services/trace-runner/app/coverage.py` | **New:** run pytest --cov, parse JSON, cache in Redis |
| `services/trace-runner/app/models.py` | `task_id`, `args`, `return_val` on TraceEvent; `coverage_pct` on RawTrace |
| `services/trace-runner/app/instrumenter_client.py` | Pass `capture_args`, `coverage_filter` to `/instrument` |
| `services/trace-normalizer/app/normalizer.py` | Async-aware call tree reconstruction |
| `services/trace-normalizer/app/models.py` | Widen TraceEvent type Literal; `coverage_pct` on ExecutionPath |
| `services/spec-generator/app/renderer.py` | Arg examples in PATH section |
| `services/spec-generator/app/consumer.py` | Use `coverage_pct` for branch_coverage |
| `services/spec-generator/app/store.py` | Prefer `coverage_pct` over frequency-ratio for branch_coverage |
| `services/spec-generator/app/models.py` | Add `coverage_pct` to ExecutionPath (if defined here) |
