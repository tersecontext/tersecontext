# Design: Codebase Readability & Performance Improvements

**Date:** 2026-03-31
**Branch:** performance_linting
**Status:** Approved

---

## Problem Statement

Three distinct problem layers have been identified:

1. **Codebase readability (for LLMs reasoning about the code):** ~14 Python services each re-implement identical FastAPI boilerplate (health/ready/metrics, lifespan context manager, Redis consumer loop, signal handlers). This forces any LLM to parse 60–80 lines of scaffolding before reaching the actual logic of a service. Additionally, 4–5 files exceed 300 lines by mixing multiple distinct concepts.

2. **Output quality (context docs for downstream LLMs):** `spec_text` does not surface edge provenance (`static/confirmed/dynamic/conflict`) or branch coverage confidence. A downstream LLM has no signal for "this call was observed at runtime" vs. "this was inferred from AST only." The `CHANGE_IMPACT` section lists tables and services as free text rather than structured fields.

3. **Performance:** Redis consumers read one event at a time. Neo4j writes in graph-enricher are per-node. The `sys.settrace` filter allows stdlib and third-party frames through before they are excluded, inflating raw event volume.

---

## Goals

- Reduce per-service cognitive load for LLMs by extracting shared boilerplate into a common base
- Decompose files that mix multiple concepts into focused modules
- Enrich `spec_text` with provenance tags, coverage confidence bands, and structured CHANGE_IMPACT
- Improve pipeline throughput with batch Redis reads, batch Neo4j writes, and a tighter sys.settrace filter

---

## Non-Goals

- Changing any service's external API or Redis stream schema
- Altering the static analysis pipeline (parser, graph-writer, embedder, vector-writer)
- Changing the query/retrieval path (dual-retriever, subgraph-expander, serializer, api-gateway)
- Rewriting Go services

---

## Section 1 — Shared Python Service Base

### New directory structure

```
shared/
  __init__.py
  service.py       # ServiceBase class
  consumer.py      # RedisConsumerBase class
  redis.py         # shared Redis client factory
  health.py        # /health, /ready, /metrics route builders
```

### ServiceBase

Wraps FastAPI app creation, lifespan setup, signal handlers, and standard endpoint registration. Each service's `main.py` reduces to:

```python
from shared.service import ServiceBase
from .consumer import MyConsumer

svc = ServiceBase("my-service", deps=["redis", "neo4j"])
svc.add_consumer(MyConsumer)
app = svc.app
```

`deps` is a list of dependency names checked during `/ready`. ServiceBase handles:
- FastAPI app instantiation
- `/health` → always 200
- `/ready` → checks each dep, returns 503 if any unavailable
- `/metrics` → Prometheus exposition
- Signal handlers for SIGTERM/SIGINT (graceful shutdown)
- Lifespan context manager

### RedisConsumerBase

Handles the XREADGROUP loop. Only applies to services that consume Redis Streams via `XREADGROUP`. Subclasses implement one method:

```python
class RedisConsumerBase:
    stream: str           # set by subclass
    group: str            # set by subclass
    consumer: str         # set by subclass

    async def handle(self, event: dict) -> None:
        raise NotImplementedError

    async def run(self) -> None:
        # XREADGROUP loop with ACK on success, XADD to DLQ on failure
        # (consistent DLQ behaviour for all stream consumers — this is a
        #  behaviour change for graph-enricher and spec-generator, which
        #  currently log-and-ACK without a DLQ)
        ...
```

**Scope of RedisConsumerBase:** three services only — graph-enricher, spec-generator, trace-normalizer. These all read from Redis Streams via `XREADGROUP`.

- `trace-runner` uses `BLPOP` against a Redis list (`entrypoint_queue:{repo}`), not `XREADGROUP`. It inherits `ServiceBase` only.
- `entrypoint-discoverer` produces to a Redis list and has no inbound consumer loop. It inherits `ServiceBase` only.
- `instrumenter` has no consumer loop (it is called via HTTP from trace-runner). It inherits `ServiceBase` only.

### Migration strategy

One service at a time, starting with graph-enricher (simplest consumer, fewest deps). Each migration is a pure refactor — behavior unchanged except for the DLQ addition noted above, which existing tests should be updated to cover.

Services to migrate `RedisConsumerBase`: graph-enricher → spec-generator → trace-normalizer.
Services to migrate `ServiceBase` only: trace-runner → entrypoint-discoverer → instrumenter.

---

## Section 2 — File Decomposition

Only files that mix multiple *concepts* are split, not files that are merely long.

### parser/extractor_go.py (302 lines) → 4 files

```
parser/
  extractor_go.py       # orchestrator: walk AST, call sub-extractors, return ParsedNode
  go_nodes.py           # function/method/type node extraction
  go_edges.py           # CALLS edge inference, import resolution
  go_stable_id.py       # stable_id + node_hash computation
```

### parser/extractor.py (276 lines) → same pattern

```
parser/
  extractor.py          # orchestrator
  py_nodes.py           # class/function node extraction
  py_edges.py           # CALLS edge inference
  py_stable_id.py       # stable_id + node_hash computation
```

### go-instrumenter/rewriter.go (318 lines) → passes pattern

```
go-instrumenter/
  rewriter.go           # orchestrator: parse, apply passes, format
  passes/entry.go       # inject tracert.Enter + defer at function entry
  passes/goroutine.go   # wrap go statements with tracert.Go
  passes/imports.go     # add/deduplicate tracert import
```

### perf-tracker/collector.py (326 lines) → 2 files

`models.py` already exists as a separate file (`PerfMetric`, `Bottleneck`, input models). Split is:

```
perf-tracker/
  collector.py          # event ingestion and routing (unchanged name)
  aggregator.py         # percentile/window computation (extracted from collector.py)
  models.py             # already exists — no change
```

### Files left as-is

- `instrumenter/main.py` (235 lines) — large but single purpose
- `trace-normalizer/main.py` (194 lines) — large but single purpose
- `go-trace-runner/run.go` (175 lines) — large but single purpose

**Rule applied:** Split when a file has multiple concepts (parsing + hashing + edge inference), not just when it is long.

---

## Section 3 — Output Enrichment (spec_text)

All changes are in `spec-generator/renderer.py`. The PATH / SIDE_EFFECTS / CHANGE_IMPACT structure is preserved — these are additive changes only.

### 1. Provenance confidence band (spec header)

```
PATH authenticate  (12 runs · HIGH confidence · 84% branch coverage)
```

Coverage bands:
- `HIGH` ≥ 80%
- `MEDIUM` 40–79%
- `LOW` < 40%

LOW specs get an explicit warning on the next line:

```
⚠ LOW COVERAGE — spec reflects partial observation only
```

### 2. Provenance tags on individual calls

Tags are appended inline on each call line:

```
PATH authenticate  (12 runs · HIGH confidence · 84% branch coverage)
  1.  validate_token       12/12 runs    ~14ms    [confirmed]
  2.  fetch_user           12/12 runs    ~28ms    [confirmed]
  3a. AuditLogger.log       8/12 runs    ~3ms     [dynamic-only]
  3b. (exits here)          4/12 runs
  ⚠ send_notification   static-only — present in AST but never observed in 12 runs
```

Tag definitions:
- `[confirmed]` — static edge observed at runtime
- `[dynamic-only]` — observed at runtime, not in AST
- `static-only` — in AST, never observed despite traced parent (surfaced as warning)

**How the renderer determines each tag:** Set-membership lookup at render time using data already on `ExecutionPath`. No model changes required.

- A call is `[dynamic-only]` if its `(caller_stable_id, callee_stable_id)` pair appears in `ExecutionPath.dynamic_only_edges`.
- A call is `[confirmed]` otherwise (present in call_sequence and not in dynamic_only_edges, meaning it was in the static graph and observed).
- A `static-only` warning is emitted for each entry in `ExecutionPath.never_observed_static_edges` — these are already tracked by the normalizer's reconciler.

Static-only edges are currently silently omitted. Surfacing them as warnings gives downstream LLMs signal about potentially dead or untested code paths.

### 3. Structured CHANGE_IMPACT

Conditions become first-class fields. The renderer derives conditionality from `SideEffect.hop_depth > 1` (already tracked) — deeper hops indicate the side effect is conditional on a branch not taken in every run. The rendered value is `(conditional)` without specific condition text, since the current data model does not capture condition predicates.

`env vars read` and `callers affected` are **excluded** from scope — the current `SideEffect` model has no `env_read` type, and caller-count data is not available to the renderer without a database query. These may be added in a future spec.

```
CHANGE_IMPACT:
  tables affected    users (r), audit_log (w · conditional)
  external services  stripe-api (conditional)
```

Verifiable DoD criterion: `SideEffect` entries with `hop_depth > 1` render as `(conditional)` inline; entries with `hop_depth == 1` render without the tag.

### Qdrant payload additions

`confidence_band` (HIGH/MEDIUM/LOW) and `coverage_pct` (float) are added to the Qdrant upsert payload for retrieval scoring.

**Computation ownership:** The renderer (`renderer.py`) computes `confidence_band` from `ExecutionPath.coverage_pct` (already a float field on the model) using the HIGH/MEDIUM/LOW thresholds above. The renderer returns both the `spec_text` string and the `confidence_band` value. The consumer passes both to `store.py`, which includes them in the Qdrant upsert payload. `coverage_pct` is passed through directly from `ExecutionPath`.

---

## Section 4 — Performance Optimizations

Three independent changes, each touching a single file. They can be shipped in any order.

### 1. Batch Redis stream consumers

**File:** `shared/consumer.py` (RedisConsumerBase — one change, all consumers inherit it)

Change `COUNT 1` to `COUNT 50, BLOCK 100`:

```python
events = await redis.xreadgroup(group, consumer, {stream: ">"}, count=50, block=100)
for event in events:
    await self.handle(event)
    await redis.xack(stream, group, event[0])
```

At high load: up to 50 events per loop iteration instead of 1, ~50x fewer round-trips.
At low load (< 50 events queued): behavior is identical to today.

### 2. Batched Neo4j writes in graph-enricher

**File:** `graph-enricher/consumer.py` (not enricher.py — the UNWIND query already exists in enricher.py)

The existing `update_node_props_batch`, `upsert_dynamic_edges`, and `confirm_static_edges` calls are currently invoked inside `_process_event` — once per event. Move them out of `_process_event` and into the batch loop in `consumer.py`: accumulate all `node_records` and `edge_records` across the full COUNT=50 read, then issue one call to each batch function at the end of the loop iteration.

This requires no Cypher changes — the UNWIND query is already correct. The change is purely in the call site.

One Neo4j round-trip per 50-event batch instead of 50 round-trips.

### 3. Tighter sys.settrace filter in instrumenter

**File:** `instrumenter/trace.py`

Add a hard exclude list applied at frame entry using full filesystem paths, not bare module names (`co_filename` is an absolute path such as `/usr/lib/python3.12/importlib/__init__.py`). Returning `None` tells CPython to stop tracing the entire subtree below that frame.

```python
import sysconfig

_STDLIB_PREFIX = sysconfig.get_path("stdlib")    # e.g. /usr/lib/python3.12
_SITE_PREFIX   = sysconfig.get_path("purelib")   # e.g. /usr/local/lib/python3.12/dist-packages

EXCLUDE_PREFIXES = (_STDLIB_PREFIX, _SITE_PREFIX)

def trace_func(frame, event, arg):
    if frame.f_code.co_filename.startswith(EXCLUDE_PREFIXES):
        return None  # prune entire subtree — no event emitted, subtree not traced
    if event in ('call', 'return', 'exception'):
        emit_event(event, frame.f_code.co_filename,
                   frame.f_code.co_name, frame.f_lineno)
    return trace_func
```

Using `sysconfig` makes the prefixes portable across environments and Python versions. The existing `coverage_filter` in `trace.py` continues to apply on top of this — the exclude list is a coarser, earlier gate.

Expected reduction in raw event volume: 60–80% on typical test suites.

---

## Implementation Order

1. **Shared base** — enables all subsequent changes to land in one place
2. **File decomposition** — independent of shared base, can proceed in parallel
3. **Output enrichment** — `spec-generator/renderer.py` only, no dependency on above
4. **Performance** — Redis batch and Neo4j batch land cleanly after shared base; sys.settrace filter is independent

---

## Definition of Done

- [ ] `shared/` package exists; graph-enricher, spec-generator, trace-normalizer use `RedisConsumerBase`; trace-runner, entrypoint-discoverer, instrumenter use `ServiceBase` only
- [ ] Each migrated service's `main.py` contains only business-logic imports and wiring
- [ ] Decomposed files: extractor_go.py, extractor.py, rewriter.go, collector.py (models.py unchanged — already exists in perf-tracker)
- [ ] `spec_text` includes confidence band header, per-call provenance tags via set-membership lookup, and static-only warnings
- [ ] CHANGE_IMPACT `(conditional)` tag rendered for `SideEffect.hop_depth > 1`; no tag for `hop_depth == 1`
- [ ] Qdrant payload includes `confidence_band` and `coverage_pct`
- [ ] Redis stream consumers (graph-enricher, spec-generator, trace-normalizer) use COUNT 50
- [ ] graph-enricher batch functions called once per 50-event loop iteration (not per event)
- [ ] `sys.settrace` uses `sysconfig`-derived path prefixes; stdlib/site-packages subtrees return `None`
- [ ] Tests updated to cover DLQ behaviour added by RedisConsumerBase for graph-enricher and spec-generator
- [ ] All existing unit and integration tests pass
