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

Handles the XREADGROUP loop. Subclasses implement one method:

```python
class RedisConsumerBase:
    stream: str           # set by subclass
    group: str            # set by subclass
    consumer: str         # set by subclass

    async def handle(self, event: dict) -> None:
        raise NotImplementedError

    async def run(self) -> None:
        # XREADGROUP loop with ACK on success, XADD to DLQ on failure
        ...
```

### Migration strategy

One service at a time, starting with graph-enricher (simplest consumer, fewest deps). Each migration is a pure refactor — behavior unchanged, existing tests verify correctness.

Services to migrate (in order): graph-enricher → spec-generator → trace-normalizer → trace-runner → entrypoint-discoverer.

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

### perf-tracker/collector.py (326 lines) → 3 files

```
perf-tracker/
  collector.py          # event ingestion and routing
  aggregator.py         # percentile/window computation
  models.py             # MetricEvent, AggregatedMetric dataclasses
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

Static-only edges are currently silently omitted. Surfacing them as warnings gives downstream LLMs signal about potentially dead or untested code paths.

### 3. Structured CHANGE_IMPACT

Conditions become first-class fields rather than free text:

```
CHANGE_IMPACT:
  tables affected    users (r), audit_log (w · conditional: role=admin)
  external services  stripe-api (conditional: amount>0 AND !sandbox_mode)
  env vars read      STRIPE_SECRET_KEY, AUDIT_ENABLED
  callers affected   12 static, 3 dynamic-only
```

### Qdrant payload additions

`confidence_band` (HIGH/MEDIUM/LOW) and `coverage_pct` (float) are added to the Qdrant upsert payload for retrieval scoring.

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

**File:** `graph-enricher/enricher.py`

Accumulate the batch from step 1 and write in one `UNWIND` query:

```cypher
UNWIND $nodes AS n
MATCH (node:Node {stable_id: n.stable_id})
SET node += {
  observed_calls:  n.observed_calls,
  avg_latency_ms:  n.avg_latency_ms,
  branch_coverage: n.branch_coverage,
  last_traced_at:  datetime()
}
```

One round-trip per batch of 50 instead of 50 round-trips.

### 3. Tighter sys.settrace filter in instrumenter

**File:** `instrumenter/trace.py`

Add a hard exclude list applied at frame entry. Returning `None` tells CPython to stop tracing the entire subtree below that frame:

```python
EXCLUDE_PREFIXES = (
    "importlib", "encodings", "logging", "threading",
    "asyncio", "unittest", "site-packages",
)

def trace_func(frame, event, arg):
    if frame.f_code.co_filename.startswith(EXCLUDE_PREFIXES):
        return None  # prune entire subtree
    if event in ('call', 'return', 'exception'):
        emit_event(event, frame.f_code.co_filename,
                   frame.f_code.co_name, frame.f_lineno)
    return trace_func
```

Expected reduction in raw event volume: 60–80% on typical test suites.

---

## Implementation Order

1. **Shared base** — enables all subsequent changes to land in one place
2. **File decomposition** — independent of shared base, can proceed in parallel
3. **Output enrichment** — `spec-generator/renderer.py` only, no dependency on above
4. **Performance** — Redis batch and Neo4j batch land cleanly after shared base; sys.settrace filter is independent

---

## Definition of Done

- [ ] `shared/` package exists and all 5 dynamic analysis services use it
- [ ] Each migrated service's `main.py` contains only business-logic imports and wiring
- [ ] Decomposed files: extractor_go.py, extractor.py, rewriter.go, collector.py
- [ ] `spec_text` includes confidence band, provenance tags, and static-only warnings
- [ ] CHANGE_IMPACT conditions are structured fields
- [ ] Qdrant payload includes `confidence_band` and `coverage_pct`
- [ ] Redis consumers use COUNT 50
- [ ] graph-enricher uses UNWIND batch write
- [ ] `sys.settrace` exclude list in place with subtree pruning
- [ ] All existing unit and integration tests pass
